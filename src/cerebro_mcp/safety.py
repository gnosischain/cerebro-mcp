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
    "FORMAT Native",
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
    # Remove single-quoted strings
    sql = re.sub(r"'[^']*'", "''", sql)
    # Remove double-quoted identifiers
    sql = re.sub(r'"[^"]*"', '""', sql)
    return sql


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

    # Check query starts with an allowed keyword
    sql_upper = sql_stripped.upper().lstrip("( \t\n\r")
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
            return False, f"Forbidden keyword detected: {keyword}"

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


def ensure_limit(sql: str, max_rows: int) -> str:
    """Append a LIMIT clause if one is not already present."""
    clean = _strip_comments_and_strings(sql)
    if not re.search(r"\bLIMIT\b", clean, re.IGNORECASE):
        sql = sql.rstrip().rstrip(";")
        sql += f"\nLIMIT {max_rows}"
    return sql
