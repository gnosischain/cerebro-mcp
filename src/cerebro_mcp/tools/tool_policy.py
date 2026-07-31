"""The canonical per-tool policy table for HTTP surface profiles.

One table drives everything: wire visibility (``tools/list``), invocation
enforcement (``tools/call``), argument restrictions (value-level, not key
presence), MCP annotations, the analysis-handle rule, and — via the frozen
sets at the bottom — the non-tool surface (resources / templates / prompts).
``find``'s tool corpus and the startup exact-set assertion read the same
table, so no discovery surface can recommend what the wire will not serve.

Profiles
--------
- ``""`` (default)         : no profile — full surface (stdio / local dev).
- ``team_analytics_v1``    : the 44-tool connector profile (R10 plan).
- ``internal_full``        : full surface, but as an EXPLICIT choice — HTTP
  transports refuse to boot without a recognized profile (fail closed), and
  the pre-connector internal deployment opts into today's behavior by name
  rather than by omission.

Scope names are ORTHOGONAL by design (tool requirements are explicit unions,
never implications), so no hierarchy logic exists anywhere.

The ``handle`` column records the analysis-handle contract (R10 / R9 P0-6).
Runtime handle enforcement lands with the handle registry (task: deglobalize
analysis state); until then the column is documentation-plus-test-surface,
NOT enforced — flipping ``HANDLE_ENFORCEMENT_ENABLED`` before that registry
exists would reject every call.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, NamedTuple

# --- profiles --------------------------------------------------------------

PROFILE_TEAM_ANALYTICS_V1 = "team_analytics_v1"
PROFILE_INTERNAL_FULL = "internal_full"
RECOGNIZED_PROFILES: frozenset[str] = frozenset(
    {PROFILE_TEAM_ANALYTICS_V1, PROFILE_INTERNAL_FULL}
)

# Not enforced yet — see module docstring.
HANDLE_ENFORCEMENT_ENABLED = False

# --- scopes ----------------------------------------------------------------

SCOPE_DISCOVER = "cerebro:discover"
SCOPE_QUERY = "cerebro:query"
SCOPE_ARTIFACT = "cerebro:artifact"

_D = frozenset({SCOPE_DISCOVER})
_DQ = frozenset({SCOPE_DISCOVER, SCOPE_QUERY})
_DA = frozenset({SCOPE_DISCOVER, SCOPE_ARTIFACT})
_DQA = frozenset({SCOPE_DISCOVER, SCOPE_QUERY, SCOPE_ARTIFACT})


class Handle(Enum):
    MINTS = "mints"        # creates and returns analysis_id (find / preflight)
    REQUIRED = "required"  # stateful: mutates or reads analysis-cycle state
    NONE = "none"          # durable owner-keyed reads; survive handle expiry


class ArgRule(NamedTuple):
    """Value-level argument restriction: deny only when ``deny(value)``."""

    arg: str
    reason: str


def _deny_truthy(value: Any) -> bool:
    """Deny on truthy VALUES only: `allow_candidate=False` and
    `research_project_id=""` are safe and pass (value-level rules, not key
    presence — R9-audit P1)."""
    return bool(value)


class Policy(NamedTuple):
    scopes: frozenset[str]
    handle: Handle
    denied_args: tuple[ArgRule, ...] = ()
    read_only: bool = True
    idempotent: bool = True
    destructive: bool = False
    open_world: bool = False


_RESEARCH_ARGS = (
    ArgRule(
        "research_project_id",
        "research persistence is not part of this profile",
    ),
    ArgRule(
        "persist_result",
        "research persistence is not part of this profile",
    ),
)
_CANDIDATE_ARG = ArgRule(
    "allow_candidate",
    "candidate (unvetted) metrics are not part of this profile",
)


def _p(
    scopes: frozenset[str],
    handle: Handle,
    *,
    denied: tuple[ArgRule, ...] = (),
    read_only: bool = True,
    idempotent: bool = True,
) -> Policy:
    return Policy(
        scopes=scopes,
        handle=handle,
        denied_args=denied,
        read_only=read_only,
        idempotent=idempotent,
    )


#: The canonical 44-entry table for ``team_analytics_v1``.
TOOL_POLICY: dict[str, Policy] = {
    # ---- mint the analysis handle ------------------------------------
    "find": _p(_D, Handle.MINTS, read_only=False),
    "preflight_analytics_request": _p(_D, Handle.MINTS, read_only=False),
    # ---- stateless discovery / metadata ------------------------------
    "get_help": _p(_D, Handle.NONE),
    "system_status": _p(_D, Handle.NONE),
    "get_platform_constants": _p(_D, Handle.NONE),
    "search_docs": _p(_D, Handle.NONE),
    "get_doc_chunk": _p(_D, Handle.NONE),
    "list_custom_tools": _p(_D, Handle.NONE),
    # `check_query` executes caller-supplied SQL and is nested INSIDE
    # claims_json (cross_check.py) — rejected per claim after parsing, not
    # by top-level key filtering. Arithmetic-only verification stays at
    # `discover`.
    "verify_numbers": _p(
        _D,
        Handle.NONE,
        denied=(
            ArgRule(
                "check_query",
                "SQL execution inside verify_numbers is not part of this "
                "profile; run the query through execute_query instead",
            ),
        ),
    ),
    # ---- state-mutating discovery (records into the analysis cycle) --
    "get_agent_persona": _p(_D, Handle.REQUIRED, read_only=False),
    "search_models": _p(_D, Handle.REQUIRED, read_only=False),
    "discover_models": _p(_D, Handle.REQUIRED, read_only=False),
    "get_model_details": _p(_D, Handle.REQUIRED, read_only=False),
    "describe_table": _p(_D, Handle.REQUIRED, read_only=False),
    "get_relevant_columns": _p(_D, Handle.REQUIRED, read_only=False),
    "get_upstream_lineage": _p(_D, Handle.REQUIRED, read_only=False),
    "get_downstream_impact": _p(_D, Handle.REQUIRED, read_only=False),
    "discover_metrics": _p(_D, Handle.REQUIRED, read_only=False),
    "get_metric_details": _p(_D, Handle.REQUIRED, read_only=False),
    "explain_metric_query": _p(
        _D, Handle.REQUIRED, denied=(_CANDIDATE_ARG,), read_only=False
    ),
    "record_model_exclusion": _p(_D, Handle.REQUIRED, read_only=False),
    "record_model_exclusion_batch": _p(_D, Handle.REQUIRED, read_only=False),
    "exclude_models_by_prefix": _p(_D, Handle.REQUIRED, read_only=False),
    "exclude_module": _p(_D, Handle.REQUIRED, read_only=False),
    "exclude_all_discovered_except": _p(_D, Handle.REQUIRED, read_only=False),
    # ---- query --------------------------------------------------------
    "execute_query": _p(
        _DQ, Handle.REQUIRED, denied=_RESEARCH_ARGS, read_only=False
    ),
    "query_metrics": _p(
        _DQ,
        Handle.REQUIRED,
        denied=_RESEARCH_ARGS + (_CANDIDATE_ARG,),
        read_only=False,
    ),
    # ---- custom YAML analytics (provenance-recording => REQUIRED) -----
    "get_validator_balance_history": _p(_DQ, Handle.REQUIRED, read_only=False),
    "get_validator_withdrawals": _p(_DQ, Handle.REQUIRED, read_only=False),
    "get_token_transfers_for_address": _p(_DQ, Handle.REQUIRED, read_only=False),
    "get_gpay_wallet_activity": _p(_DQ, Handle.REQUIRED, read_only=False),
    "get_liquidity_providers_by_token": _p(_DQ, Handle.REQUIRED, read_only=False),
    "get_bridge_flows_by_token": _p(_DQ, Handle.REQUIRED, read_only=False),
    "get_deposit_events": _p(_DQ, Handle.REQUIRED, read_only=False),
    # ---- chart generation ---------------------------------------------
    "generate_chart": _p(_DQA, Handle.REQUIRED, read_only=False, idempotent=False),
    "quick_chart": _p(_DQA, Handle.REQUIRED, read_only=False, idempotent=False),
    "generate_charts": _p(_DQA, Handle.REQUIRED, read_only=False, idempotent=False),
    "quick_metric_chart": _p(_DQA, Handle.REQUIRED, read_only=False, idempotent=False),
    "generate_metric_charts": _p(
        _DQA, Handle.REQUIRED, read_only=False, idempotent=False
    ),
    # ---- artifacts ----------------------------------------------------
    "list_charts": _p(_DA, Handle.REQUIRED),
    "generate_report": _p(_DA, Handle.REQUIRED, read_only=False, idempotent=False),
    # Durable owner-keyed reads: must keep working after handle expiry.
    "list_reports": _p(_DA, Handle.NONE),
    "open_report": _p(_DA, Handle.NONE),
    # Minting a stateless capability link is not an external mutation.
    "export_report": _p(_DA, Handle.NONE),
}

CONNECTOR_TOOL_NAMES: frozenset[str] = frozenset(TOOL_POLICY)
assert len(TOOL_POLICY) == 44, (
    f"TOOL_POLICY must hold exactly 44 entries, found {len(TOOL_POLICY)}"
)


# --- non-tool surface for team_analytics_v1 --------------------------------

#: The seven static reference resources.
RESOURCES_ALLOWED: frozenset[str] = frozenset(
    {
        "gnosis://platform-overview",
        "gnosis://clickhouse-sql-guide",
        "gnosis://chain-parameters",
        "gnosis://address-directory",
        "gnosis://metric-definitions",
        "gnosis://query-cookbook",
        "gnosis://semantic-graph-overview",
    }
)

#: Allowed templates (two — `source-tables/{database}` is deliberately
#: dropped: with only `dbt` reachable it is pointless, and the semantic-*
#: template content is reachable through allowed tools).
TEMPLATE_PREFIXES_ALLOWED: tuple[str, ...] = (
    "gnosis://dbt-modules/",
    "gnosis://semantic-model/",
)
TEMPLATE_URI_TEMPLATES_ALLOWED: frozenset[str] = frozenset(
    {
        "gnosis://dbt-modules/{module_name}",
        "gnosis://semantic-model/{name}",
    }
)

#: Prompts on the connector profile: none. list_prompts is empty and
#: get_prompt rejects by name.
PROMPTS_ALLOWED: frozenset[str] = frozenset()


# --- runtime helpers -------------------------------------------------------


def active_profile() -> str:
    """The live surface profile ('' when none is configured)."""
    from cerebro_mcp.config import settings

    return getattr(settings, "MCP_SURFACE_PROFILE", "") or ""


def connector_profile_active() -> bool:
    return active_profile() == PROFILE_TEAM_ANALYTICS_V1


# --- ClickHouse relation boundary for team_analytics_v1 --------------------

#: Databases the connector's caller SQL may touch (A1 decision).
CONNECTOR_ALLOWED_DATABASES: frozenset[str] = frozenset({"dbt"})

#: Explicit single-relation grants outside those databases.
#: consensus.specs backs gnosis://chain-parameters (public chain constants).
CONNECTOR_ALLOWED_TABLES: frozenset[tuple[str, str]] = frozenset(
    {("consensus", "specs")}
)


def connector_relation_allowed(database: str, table: str) -> bool:
    """Is (database, table) reachable under the connector profile?

    Callers must gate on :func:`connector_profile_active` themselves — this
    answers only the membership question so it can be used in error paths
    without re-reading settings.
    """
    return (
        database in CONNECTOR_ALLOWED_DATABASES
        or (database, table) in CONNECTOR_ALLOWED_TABLES
    )


def tool_visible(name: str) -> bool:
    """Wire visibility under the active profile (tools/list)."""
    if not connector_profile_active():
        return True
    return name in TOOL_POLICY


class ToolPolicyViolation(Exception):
    """A tools/call rejected by the connector profile policy."""


def check_call_allowed(name: str, arguments: dict[str, Any] | None) -> None:
    """Invocation enforcement (tools/call). Raises ToolPolicyViolation.

    Listing and invocation are enforced SEPARATELY on purpose: hiding a name
    from tools/list is not a capability boundary (R10 §blocker 2).
    """
    if not connector_profile_active():
        return
    policy = TOOL_POLICY.get(name)
    if policy is None:
        raise ToolPolicyViolation(
            f"tool {name!r} is not part of the {PROFILE_TEAM_ANALYTICS_V1} "
            "profile"
        )
    args = arguments or {}
    for rule in policy.denied_args:
        if rule.arg in args and _deny_truthy(args[rule.arg]):
            raise ToolPolicyViolation(
                f"argument {rule.arg!r} is not permitted on this profile: "
                f"{rule.reason}"
            )
    if name == "verify_numbers":
        _reject_nested_check_query(args)


def _reject_nested_check_query(arguments: dict[str, Any]) -> None:
    """Reject `check_query` nested inside verify_numbers' claims payload.

    The SQL rides INSIDE the claims JSON (cross_check.py parses per-claim
    `check_query`), so top-level argument filtering cannot see it. Malformed
    JSON passes through here — the tool itself reports the parse error.
    """
    raw = arguments.get("claims_json")
    if not raw:
        return
    try:
        claims = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return
    if isinstance(claims, dict):
        claims = [claims]
    if not isinstance(claims, list):
        return
    for claim in claims:
        if isinstance(claim, dict) and _deny_truthy(claim.get("check_query")):
            raise ToolPolicyViolation(
                "check_query inside claims_json is not permitted on this "
                "profile; run the SQL through execute_query instead"
            )


def resource_uri_allowed(uri: str) -> bool:
    """Concrete-resource read policy under the connector profile."""
    if not connector_profile_active():
        return True
    if uri in RESOURCES_ALLOWED:
        return True
    return any(uri.startswith(p) for p in TEMPLATE_PREFIXES_ALLOWED)


def template_allowed(uri_template: str) -> bool:
    if not connector_profile_active():
        return True
    return uri_template in TEMPLATE_URI_TEMPLATES_ALLOWED


def prompt_allowed(name: str) -> bool:
    if not connector_profile_active():
        return True
    return name in PROMPTS_ALLOWED


def assert_exact_surface(registered: set[str]) -> None:
    """Startup fail-closed check: the wire set must be EXACTLY the 44.

    Runs over the post-profile-filter set only — never the raw registry
    (SEMANTIC_ENABLED also registers `reload_semantic_registry` and
    `get_clickhouse_query_rules`, neither in the profile; the raw
    semantic-on surface takes two different sizes).
    """
    visible = {n for n in registered if n in TOOL_POLICY}
    missing = CONNECTOR_TOOL_NAMES - visible
    if missing:
        raise RuntimeError(
            f"{PROFILE_TEAM_ANALYTICS_V1}: {len(missing)} profile tool(s) "
            f"not registered: {sorted(missing)}. Likely cause: "
            "CUSTOM_TOOLS_ENABLED/CUSTOM_TOOLS_PATH (8 tools) or "
            "SEMANTIC_ENABLED (6 tools) unset. Refusing to serve a "
            "silently smaller connector surface."
        )
