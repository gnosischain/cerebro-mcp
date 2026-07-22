"""Static tool metadata for the `find` router.

`find` ranks the whole tool surface (not just semantic tools), so it needs a
compact, per-tool descriptor beyond the raw docstring: a coarse ``domain`` (so
hits can be grouped / gently boosted), free-text ``tags`` (extra search terms
BM25 wouldn't get from the name alone), and a ``tier`` (``core`` = a common
entry point, ``advanced`` = long-tail). This mirrors the OpenMetadata "domain +
tier" idea already used for models/metrics in the data catalog.

Source of truth precedence for a tool's descriptor (see :func:`classify_tool`):

1. An explicit :data:`TOOL_META` entry.
2. Otherwise inferred: domain from the name/risk registry, tier defaults to
   ``advanced``, tags from the docstring's first line.

Dynamic/custom tools (from ``custom_tools.yaml``) are auto-classified and
EXEMPT from the coverage lint — only STATIC (hard-coded) tools that are missing
a :data:`TOOL_META` entry AND unknown to the risk registry hard-fail the lint,
because those are the ones a developer forgot to describe.
"""

from __future__ import annotations

from cerebro_mcp.security import RiskClass, TOOL_RISK_REGISTRY, primary_risk_class

# ---------------------------------------------------------------------------
# Static per-tool metadata.
#
# Keep this hand-curated for the STATIC tool surface. `tier="core"` marks the
# small set of everyday entry points `find` should prefer to surface (metrics,
# raw discovery, charting, reports, help). Everything else defaults to
# `tier="advanced"` via inference and does not need an entry here.
# ---------------------------------------------------------------------------

Meta = dict[str, object]

# The lean-core surface (Phase 3). This is the ~17-tool set that stays visible
# when ``LEAN_CORE_ENABLED`` is on — every common ``find`` follow-up for answer
# AND chart/report modes, so ``find(mode="chart"|"report")`` works without
# needing ``load_tools``. It is the authoritative list; the ``tier="core"``
# entries in :data:`TOOL_META` below MUST match it exactly (guarded by
# :func:`core_tool_names` / a test). To add/remove a core tool, edit BOTH here
# and the corresponding TOOL_META tier.
CORE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "find",
        "query_metrics",
        "execute_query",
        "describe_table",
        "get_model_details",
        "get_metric_details",
        "explain_metric_query",
        "preflight_analytics_request",
        "quick_chart",
        "quick_metric_chart",
        "generate_chart",
        "generate_charts",
        "generate_metric_charts",
        "generate_report",
        "get_help",
        "system_status",
        "verify_numbers",
        # The lean-core escape hatch itself must stay visible so a client can
        # un-hide advanced tools when the flag is on. (This is the one addition
        # beyond the plan's 17-tool answer/chart/report set.)
        "load_tools",
    }
)

TOOL_META: dict[str, Meta] = {
    # ── semantic / metrics (core) ────────────────────────────────────
    "find": {"domain": "discovery", "tier": "core", "tags": ["search", "router", "entry", "route"]},
    "preflight_analytics_request": {
        "domain": "semantic",
        "tier": "core",
        "tags": ["route", "plan", "gate", "analytics"],
    },
    "query_metrics": {
        "domain": "semantic",
        "tier": "core",
        "tags": ["metric", "aggregate", "kpi", "answer", "time series"],
    },
    "discover_metrics": {"domain": "semantic", "tier": "advanced", "tags": ["metric", "search"]},
    "get_metric_details": {"domain": "semantic", "tier": "core", "tags": ["metric", "definition"]},
    "explain_metric_query": {"domain": "semantic", "tier": "core", "tags": ["metric", "sql", "explain"]},
    "quick_metric_chart": {"domain": "visualization", "tier": "core", "tags": ["metric", "chart", "plot"]},
    "generate_metric_charts": {"domain": "visualization", "tier": "core", "tags": ["metric", "chart", "report"]},
    # ── raw discovery / schema (core) ────────────────────────────────
    "search_models": {"domain": "discovery", "tier": "advanced", "tags": ["model", "dbt", "search"]},
    "discover_models": {"domain": "discovery", "tier": "advanced", "tags": ["model", "dbt", "search", "details"]},
    "get_model_details": {"domain": "discovery", "tier": "core", "tags": ["model", "lineage", "columns"]},
    "describe_table": {"domain": "schema", "tier": "core", "tags": ["table", "columns", "schema", "types"]},
    "execute_query": {"domain": "query", "tier": "core", "tags": ["sql", "clickhouse", "raw", "query"]},
    "get_sample_data": {"domain": "schema", "tier": "advanced", "tags": ["rows", "preview", "sample"]},
    # ── listing family (advanced) ────────────────────────────────────
    # `list(kind=...)` is the Phase-4 unifier; the five `list_*` tools are now
    # thin deprecating shims over the same helpers. All advanced: hidden under
    # LEAN_CORE_ENABLED but still callable during the deprecation window.
    "list": {"domain": "meta", "tier": "advanced", "tags": ["list", "tables", "databases", "charts", "reports", "saved queries"]},
    "list_tables": {"domain": "schema", "tier": "advanced", "tags": ["tables", "list"]},
    "list_databases": {"domain": "schema", "tier": "advanced", "tags": ["databases", "list"]},
    "list_charts": {"domain": "visualization", "tier": "advanced", "tags": ["charts", "list", "registry"]},
    "list_reports": {"domain": "reporting", "tier": "advanced", "tags": ["reports", "list", "saved"]},
    "list_saved_queries": {"domain": "query", "tier": "advanced", "tags": ["saved", "queries", "list"]},
    # ── charting / reporting (core) ──────────────────────────────────
    "quick_chart": {"domain": "visualization", "tier": "core", "tags": ["chart", "plot", "adhoc"]},
    "generate_chart": {"domain": "visualization", "tier": "core", "tags": ["chart", "plot"]},
    "generate_charts": {"domain": "visualization", "tier": "core", "tags": ["chart", "batch", "report"]},
    "generate_report": {"domain": "reporting", "tier": "core", "tags": ["report", "dashboard", "html"]},
    "export_report": {"domain": "reporting", "tier": "advanced", "tags": ["report", "export", "html"]},
    # ── verification / help (core) ───────────────────────────────────
    "verify_numbers": {"domain": "governance", "tier": "core", "tags": ["verify", "check", "arithmetic"]},
    "get_help": {"domain": "meta", "tier": "core", "tags": ["help", "guide", "how to"]},
    "system_status": {"domain": "meta", "tier": "core", "tags": ["status", "health", "diagnostics"]},
    "get_platform_constants": {"domain": "meta", "tier": "advanced", "tags": ["constants", "chain", "gnosis"]},
    "list_custom_tools": {"domain": "meta", "tier": "advanced", "tags": ["custom", "tools", "list"]},
    "load_tools": {"domain": "meta", "tier": "core", "tags": ["load", "unhide", "advanced", "tools"]},
    # ── catalog (advanced) ───────────────────────────────────────────
    "catalog_search": {"domain": "discovery", "tier": "advanced", "tags": ["catalog", "search", "browse"]},
    "open_cow_explorer": {
        "domain": "visualization",
        "tier": "advanced",
        "tags": ["cow", "orders", "trades", "prices", "auctions", "solvers", "orderbook"],
    },
    "open_governance": {
        "domain": "visualization",
        "tier": "advanced",
        "tags": ["governance", "snapshot", "gnosisdao", "proposals", "votes", "forum", "gip", "quorum"],
    },
    # ── web3 / rpc (advanced) ────────────────────────────────────────
    "contract_explore": {"domain": "web3", "tier": "advanced", "tags": ["contract", "abi", "address"]},
    "contract_call_function": {"domain": "web3", "tier": "advanced", "tags": ["contract", "call", "read"]},
    "resolve_address": {"domain": "web3", "tier": "advanced", "tags": ["address", "ens", "resolve"]},
}


# Coarse domain inference from a tool-name prefix, used when a tool has no
# TOOL_META entry. Ordered longest/most-specific first.
_DOMAIN_NAME_HINTS: tuple[tuple[str, str], ...] = (
    ("storyteller_", "storyteller"),
    ("rpc_", "web3"),
    ("contract_", "web3"),
    ("grafana", "reporting"),
    ("dashboard", "reporting"),
    ("research", "research"),
    ("chart", "visualization"),
    ("report", "reporting"),
    ("metric", "semantic"),
    ("semantic", "semantic"),
    ("model", "discovery"),
    ("table", "schema"),
    ("query", "query"),
    ("get_", "discovery"),
    ("list_", "meta"),
    ("open_", "visualization"),
    ("load_", "visualization"),
)

# Risk class → coarse domain, the second inference source (the risk registry is
# the authoritative record of a tool's side-effect surface).
_RISK_DOMAIN: dict[RiskClass, str] = {
    RiskClass.SUBPROCESS: "governance",
    RiskClass.EXTERNAL_WRITE: "reporting",
    RiskClass.WORKSPACE_WRITE: "governance",
    RiskClass.SERVER_STATE_WRITE: "governance",
    RiskClass.APP_ONLY: "visualization",
    RiskClass.READ_ONLY: "discovery",
}


def _infer_domain(name: str) -> str:
    for prefix, domain in _DOMAIN_NAME_HINTS:
        if prefix in name:
            return domain
    return _RISK_DOMAIN.get(primary_risk_class(name), "discovery")


def _tags_from_doc(description: str) -> list[str]:
    """Cheap keyword tags from a docstring's first line (lowercased words > 3
    chars). Only used when TOOL_META has no explicit tags."""
    first = (description or "").strip().splitlines()[0] if description else ""
    seen: list[str] = []
    for raw in first.replace("/", " ").replace("-", " ").split():
        word = "".join(ch for ch in raw.lower() if ch.isalnum())
        if len(word) > 3 and word not in seen:
            seen.append(word)
        if len(seen) >= 8:
            break
    return seen


def classify_tool(name: str, description: str = "") -> dict[str, object]:
    """Return ``{domain, tier, tags}`` for a tool.

    Explicit :data:`TOOL_META` wins. Otherwise domain is inferred from the name
    / risk registry, tier defaults to ``advanced``, and tags fall back to the
    docstring's first line. Never raises — an unknown tool still classifies.
    """
    meta = TOOL_META.get(name)
    if meta is not None:
        tags = list(meta.get("tags", []) or [])  # type: ignore[arg-type]
        return {
            "domain": str(meta.get("domain") or _infer_domain(name)),
            "tier": str(meta.get("tier") or "advanced"),
            "tags": tags or _tags_from_doc(description),
        }
    return {
        "domain": _infer_domain(name),
        "tier": "advanced",
        "tags": _tags_from_doc(description),
    }


def is_core_tool(name: str, description: str = "") -> bool:
    """True if ``name`` classifies as ``tier="core"`` (part of the lean surface).

    Thin wrapper over :func:`classify_tool` so the visibility filter has one
    obvious call. Never raises — an unknown tool is ``advanced`` (not core).
    """
    return classify_tool(name, description).get("tier") == "core"


def core_tool_names() -> frozenset[str]:
    """The authoritative lean-core tool names (see :data:`CORE_TOOL_NAMES`)."""
    return CORE_TOOL_NAMES


def lint_tool_meta_coverage(
    static_tool_names: set[str],
    *,
    dynamic_tool_names: set[str] | None = None,
) -> list[str]:
    """Return the names of STATIC tools that lack any classifiable metadata.

    A tool is considered covered if it has a :data:`TOOL_META` entry OR appears
    in :data:`TOOL_RISK_REGISTRY` (the risk registry is enough to infer a
    domain). Dynamic/custom tools are auto-classified and exempt — they are
    never returned even if absent from both maps.

    Callers hard-fail (raise/log) only on a non-empty result, per the plan:
    only a developer-authored STATIC tool with neither meta nor a risk-registry
    entry is a real gap.
    """
    dynamic = dynamic_tool_names or set()
    missing: list[str] = []
    for name in sorted(static_tool_names):
        if name in dynamic:
            continue
        if name in TOOL_META or name in TOOL_RISK_REGISTRY:
            continue
        missing.append(name)
    return missing
