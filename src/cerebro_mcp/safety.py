import re
from typing import Tuple


# SQL keywords that indicate write/DDL operations
FORBIDDEN_KEYWORDS = [
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "RENAME",
    "ATTACH",
    "DETACH",
    "OPTIMIZE",
    "GRANT",
    "REVOKE",
    "KILL",
    "SYSTEM",
    "INTO OUTFILE",
]

FORBIDDEN_PATTERNS = [
    r"\bFORMAT\s+\w+\b",
    r"\bSETTINGS\b",
    # Table functions and cross-relation wrappers. Grants do not control
    # table functions (they are a separate ClickHouse privilege class), so
    # the application layer denies them outright; the connector identity
    # additionally receives no SOURCES grants. The legacy list covered only
    # eight — remoteSecure/sqlite/merge/cluster* et al. sailed through
    # (proven 2026-07-31; see tests/test_relation_boundary.py).
    r"\b(file|url|s3|hdfs|jdbc|mysql|postgresql|odbc|remote|remoteSecure"
    r"|urlCluster|s3Cluster|hdfsCluster|azureBlobStorage|deltaLake|iceberg"
    r"|hudi|mongodb|redis|sqlite|executable|input|cluster"
    r"|clusterAllReplicas|merge|generateRandom|fileCluster)\s*\(",
    # The dictionary access family (dictGet, dictGetString, dictHas,
    # dictionary(...)) reads outside the grant system entirely.
    r"\bdict\w*\s*\(",
    # format(<BareIdentifier>, ...) is the TABLE function (reads inline
    # data); format('...') the string function has a quoted first argument
    # and, after literal stripping, never matches this.
    r"\bformat\s*\(\s*[A-Za-z_]",
]

# Allowed query starts
ALLOWED_PREFIXES = ("SELECT", "EXPLAIN", "DESCRIBE", "SHOW", "WITH", "EXISTS")

# Valid table/database name pattern
TABLE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def _strip_comments_and_strings(sql: str) -> str:
    """Remove string literals and comments to avoid false positives on keyword detection."""
    # Remove single-line comments
    sql = re.sub(r"--[^\n]*", " ", sql)
    # Remove multi-line comments
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # Normalize backtick-quoted identifiers (`db`.`tbl`) to bare form. Without
    # this, ClickHouse/MySQL backtick quoting bypasses both keyword detection
    # and table-name extraction — e.g. `dbt`.`int_..._bridge` would never match
    # the internal-only deny list. Done before quote removal so the bare
    # identifiers survive into the cleaned SQL.
    sql = re.sub(r"`([^`]*)`", r"\1", sql)
    # Remove single-quoted strings
    sql = re.sub(r"'[^']*'", "''", sql)
    # Remove double-quoted identifiers
    sql = re.sub(r'"[^"]*"', '""', sql)
    return sql


# Internal-only tables: hardcoded deny list for `execute_query`-style raw
# SQL access. These tables hold raw addresses + pseudonyms together (the
# pseudonymization boundary) and must never be queryable by an MCP caller,
# even by exact-name reference. The manifest_loader already filters them
# out of `search_models` / `discover_models` indexes, but a determined
# caller could in principle hardcode the table name; this list is the
# defence-in-depth check.
#
# Seeded with the known identity bridges; the manifest loader extends this at
# load time with EVERY model tagged `internal_only` / `privacy:tier_internal`
# (see register_internal_only_tables), so the deny list stays complete without
# hardcoding all names here. Mutable on purpose — checked live by validate_query.
INTERNAL_ONLY_TABLES: set[str] = {
    "int_execution_gnosis_app_user_identity_bridge",
    "int_execution_gpay_user_identity_bridge",
}


def register_internal_only_tables(names) -> None:
    """Extend the internal-only deny list at runtime (called by the manifest
    loader with the full set of internal-tagged model/alias names)."""
    for name in names or ():
        if name:
            INTERNAL_ONLY_TABLES.add(str(name))


#: What to do INSTEAD, per blocked keyword.
#:
#: "Forbidden keyword detected: SYSTEM" is the single most common error on the
#: deployed platform — 14 occurrences across the last 6 sessions. Measured cost in
#: one live session: the caller queried `system.tables`, got the bare refusal,
#: retried `system.tables` with a different WHERE, tried `information_schema`
#: (also blocked), tried an unqualified table name (UNKNOWN_TABLE), and only then
#: found `list_tables`. Four wasted round-trips to rediscover a tool the server
#: already exposes.
#:
#: A refusal that does not say what to do instead invites the retry. SYSTEM is the
#: only entry because it is the only forbidden keyword a caller reaches for when
#: they want SCHEMA rather than to write — every other one (INSERT, DROP, GRANT,
#: KILL …) is a genuine write with no read-only alternative to point at. `SHOW` is
#: deliberately absent: it is an ALLOWED prefix, not a forbidden keyword, so an
#: entry for it could never fire.
_KEYWORD_ALTERNATIVES = {
    "SYSTEM": (
        " — the `system` database is not queryable. For schema use the tools: "
        "`list_tables(database=...)` for tables, `describe_table(database=..., "
        "table=...)` for columns, `search_models(query=...)` to find a model."
    ),
}


def _keyword_hint(keyword: str) -> str:
    return _KEYWORD_ALTERNATIVES.get(keyword.upper(), "")


def validate_query(sql: str, max_length: int = 10000) -> Tuple[bool, str]:
    """Validate a SQL query for safety.

    Returns (is_valid, error_message). If is_valid is True, error_message is empty.
    """
    if not sql or not sql.strip():
        return False, "Empty query"

    sql_stripped = sql.strip()

    # Check query length
    if len(sql_stripped) > max_length:
        return False, f"Query exceeds maximum length of {max_length} characters"

    # Strip leading comments before checking prefix
    sql_no_leading_comments = re.sub(
        r"^(\s*(--[^\n]*\n|/\*.*?\*/\s*))+",
        "",
        sql_stripped,
        flags=re.DOTALL,
    ).strip()
    sql_upper = sql_no_leading_comments.upper().lstrip("( \t\n\r")
    if not any(sql_upper.startswith(prefix) for prefix in ALLOWED_PREFIXES):
        return False, (
            f"Query must start with one of: {', '.join(ALLOWED_PREFIXES)}. "
            f"Got: {sql_stripped[:50]}..."
        )

    # Check for multiple statements (semicolon followed by non-whitespace)
    # Allow trailing semicolons but not mid-query ones
    clean_sql = _strip_comments_and_strings(sql_stripped)
    parts = clean_sql.split(";")
    non_empty_parts = [p.strip() for p in parts if p.strip()]
    if len(non_empty_parts) > 1:
        return False, "Multiple SQL statements are not allowed"

    # Check for forbidden keywords in the cleaned SQL
    clean_upper = clean_sql.upper()
    for keyword in FORBIDDEN_KEYWORDS:
        # Match as whole word(s) to avoid false positives
        pattern = r"\b" + r"\s+".join(keyword.split()) + r"\b"
        if re.search(pattern, clean_upper):
            return False, f"Forbidden keyword detected: {keyword}{_keyword_hint(keyword)}"

    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, clean_sql, flags=re.IGNORECASE):
            return False, f"Forbidden SQL pattern detected: {pattern}"

    # Relation-level checks. extract_table_names returns "db.table" or bare
    # "table"; both forms are validated here so a qualified reference can
    # never bypass a check that a bare one would hit (GDPR-audit M1: only
    # the CONNECTION database was validated, so
    # `execute_query(sql="SELECT ... FROM mixpanel_ga.<t>", database="dbt")`
    # passed every application-layer control).
    from cerebro_mcp.config import settings as _settings

    allowed_dbs = set(_settings.ALLOWED_DATABASES)
    for table in extract_table_names(sql):
        db_part, _, bare_name = table.rpartition(".")

        # Privacy boundary: internal-only tables (raw + pseudonym pairing)
        # are denied in bare AND qualified form.
        if bare_name in INTERNAL_ONLY_TABLES:
            return False, (
                f"Table '{bare_name}' is internal-only (raw + pseudonym "
                f"pairing) and cannot be queried via execute_query. "
                f"Pseudonym-keyed marts in the same sector are queryable; "
                f"see the GA/GP MTA stack docs."
            )

        # Qualifier allowlist: a db-qualified reference must name an allowed
        # database, regardless of which connection database was requested.
        if db_part and db_part not in allowed_dbs:
            return False, (
                f"Database '{db_part}' is not allowed. Allowed: "
                f"{', '.join(sorted(allowed_dbs))}"
            )

        # Connector-profile narrowing: caller SQL reaches dbt plus the
        # explicit consensus.specs grant, nothing else. The authority for
        # the sets is tools/tool_policy.py (imported lazily — safety is
        # loaded very early).
        if db_part:
            from cerebro_mcp.tools.tool_policy import (
                connector_profile_active,
                connector_relation_allowed,
            )

            if connector_profile_active() and not connector_relation_allowed(
                db_part, bare_name
            ):
                return False, (
                    f"Relation '{table}' is outside the connector profile "
                    f"(dbt.* plus consensus.specs only)."
                )

    return True, ""


def validate_relation_access(database: str, table: str) -> Tuple[bool, str]:
    """Relation authorization for the TYPED metadata path (describe_table).

    Free SQL against ``system.*`` stays keyword-blocked; the typed schema
    functions authorize the (database, table) they were asked about BEFORE
    running their fixed parameterized ``system.columns`` query — so schema
    introspection obeys the same boundary as data access.
    """
    from cerebro_mcp.config import settings as _settings
    from cerebro_mcp.tools.tool_policy import (
        connector_profile_active,
        connector_relation_allowed,
    )

    if table in INTERNAL_ONLY_TABLES:
        return False, f"Table '{table}' is internal-only."
    if database not in set(_settings.ALLOWED_DATABASES):
        return False, f"Database '{database}' is not allowed."
    if connector_profile_active() and not connector_relation_allowed(
        database, table
    ):
        return False, (
            f"Relation '{database}.{table}' is outside the connector "
            f"profile (dbt.* plus consensus.specs only)."
        )
    return True, ""


def validate_identifier(name: str) -> Tuple[bool, str]:
    """Validate a table or database name."""
    if not name:
        return False, "Empty identifier"
    if not TABLE_NAME_RE.match(name):
        return False, (
            f"Invalid identifier '{name}'. "
            "Only alphanumeric characters and underscores are allowed."
        )
    return True, ""


def enforce_result_limit(sql: str, max_rows: int) -> str:
    """Guarantee a result cap even when the original query already has LIMIT."""
    clean = _strip_comments_and_strings(sql)
    base = sql.rstrip().rstrip(";")
    if not re.search(r"\bLIMIT\b", clean, re.IGNORECASE):
        return f"{base}\nLIMIT {max_rows}"
    return f"SELECT * FROM ({base}) AS _cerebro_limit LIMIT {max_rows}"


def ensure_limit(sql: str, max_rows: int) -> str:
    """Backward-compatible alias for result-cap enforcement."""
    return enforce_result_limit(sql, max_rows)


_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:(\w+)\.)?(\w+)",
    re.IGNORECASE,
)


def extract_table_names(sql: str) -> list[str]:
    """Best-effort extraction of table names from a read-only SQL query.

    Returns deduplicated names in encounter order.
    Covers FROM and JOIN clauses, which is sufficient for the read-only
    SELECT queries allowed through ``validate_query()``.
    """
    cleaned = _strip_comments_and_strings(sql)
    seen: set[str] = set()
    tables: list[str] = []
    for match in _TABLE_REF_RE.finditer(cleaned):
        db_part, table = match.group(1), match.group(2)
        if table.startswith("_cerebro"):
            continue
        qualified = f"{db_part}.{table}" if db_part else table
        if qualified not in seen:
            seen.add(qualified)
            tables.append(qualified)
    return tables


# ---------------------------------------------------------------------------
# ReplacingMergeTree read hygiene
# ---------------------------------------------------------------------------
#
# Implements the DETECTION heuristics of the `ch-final-three-way-rule` lesson
# (src/cerebro_mcp/prompts/lessons/ch-final-three-way-rule.md), which is the
# arbiter for a rule stated in six places with wording that reads as
# contradictory. These are WARNINGS, never rejections: the branches depend on
# the physical relation, and a regex cannot always tell which one applies.
#
# Why this exists: the mini-app SQL specs are test-guarded
# (test_every_spec_targets_governance_db_with_final_order_by_and_binds), but
# AD-HOC model-authored SQL through execute_query had no check at all. Omitting
# FINAL on a raw ReplacingMergeTree does not error — duplicate generations
# survive until a background merge, so counts and sums come back silently
# INFLATED. A wrong number that looks plausible is worse than a failure.

#: Raw ReplacingMergeTree plane where FINAL is MANDATORY (branch 1).
#: Deliberately NOT cow_db: its large tables are branch 3, where FINAL is
#: forbidden because it OOMs (code 241), so a "missing FINAL" warning there
#: would be actively wrong advice.
_FINAL_REQUIRED_DB = "governance_db"

#: Canonical views that resolve dedup internally (branch 2) — FINAL forbidden.
#: Matched by the `v_` prefix, which is the repo's convention for them.
_CANONICAL_VIEW_PREFIX = "v_"

#: Not job-scoped, so an unpinned read spans every census job and
#: double-counts any token measured twice.
_JOB_SCOPED_VIEW = "v_treasury_balances"

_FINAL_AFTER_REF_RE = re.compile(r"\s+(AS\s+\w+\s+)?FINAL\b", re.IGNORECASE)


def dedup_hygiene_warnings(sql: str) -> list[str]:
    """Warn on ReplacingMergeTree reads that silently return wrong numbers.

    Returns human-readable warnings; an empty list means nothing detected.
    Comments and string literals are stripped first, so a ``-- FINAL`` comment
    never satisfies the check.
    """
    cleaned = _strip_comments_and_strings(sql)
    warnings: list[str] = []

    # Branch 1 — raw table without FINAL.
    for match in re.finditer(rf"\b{_FINAL_REQUIRED_DB}\.([a-zA-Z_][a-zA-Z0-9_]*)", cleaned):
        table = match.group(1)
        if table.startswith(_CANONICAL_VIEW_PREFIX):
            continue  # branch 2: dedup resolved inside the view
        if not _FINAL_AFTER_REF_RE.match(cleaned[match.end():]):
            warnings.append(
                f"DEDUP RISK: {_FINAL_REQUIRED_DB}.{table} is a ReplacingMergeTree "
                "re-inserted daily and this read has no FINAL, so several "
                "generations are counted at once — counts and sums are inflated "
                "with no error. Use `FROM "
                f"{_FINAL_REQUIRED_DB}.{table} FINAL` (alias BEFORE FINAL). "
                "See lesson ch-final-three-way-rule."
            )

    # Branch 2 rider — the job-scoped view read without its job pin.
    if _JOB_SCOPED_VIEW in cleaned and "job_name" not in cleaned:
        warnings.append(
            f"DEDUP RISK: {_JOB_SCOPED_VIEW} is not job-scoped; without a "
            "`job_name` predicate this spans every census job (185M+ rows) and "
            "double-counts any token measured twice. Pin job_name."
        )

    # Scratch scan tables are ReplacingMergeTree too — a bare count() overcounts
    # after a resumed scan. The lesson notes this variant had no test anywhere.
    if re.search(r"\bscratch\.rpc_[a-zA-Z0-9_]+", cleaned) and re.search(
        r"\bcount\s*\(", cleaned, re.IGNORECASE
    ):
        if not re.search(r"\buniqExact\s*\(", cleaned, re.IGNORECASE):
            warnings.append(
                "DEDUP RISK: scratch.rpc_* tables are ReplacingMergeTree, so a "
                "bare count() overcounts after a resumed scan. Use uniqExact() "
                "on the identity column, or FINAL."
            )

    return warnings
