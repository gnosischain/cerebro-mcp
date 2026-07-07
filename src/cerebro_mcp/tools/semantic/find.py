"""`find` — a single semantic front door for the whole tool surface.

One call answers "what do I use for X?" by ranking, in one payload:

- **tools**   (BM25 over the whole registered-tool corpus, enriched with
              risk-registry + `tool_meta` domain/tags, APP_ONLY excluded),
- **metrics** (from the shared `_route` routing core),
- **models**  (via `catalog_search(entity_types=["model"])`), and
- a mode-aware **recommended_action** (next tool + pre-filled args).

Design invariants (see the approved plan):

* Routing metrics/dimensions come from the SHARED `_route(query, mode)` core —
  `find` and `preflight_analytics_request(detail="slim")` never diverge.
* `find` records state via `state.record_semantic_find(...)`, which sets
  `semantic_find_ran` — NOT `semantic_preflight_ran`. An answer-mode `find`
  unblocks raw discovery for free but must not open the chart/report hard gates.
* The tool corpus is built LAZILY on first call (tools register after the
  semantic tools, so an eager build would miss custom / mini-app / data-catalog
  tools). It is cached and rebuilt only when the registered-tool set changes.
* We do NOT touch `catalog_search`'s `ENTITY_TYPES` — the tool index lives here,
  entirely separate from the Data Catalog browse universe.
"""

from __future__ import annotations

import threading
from typing import Any

from cerebro_mcp.config import settings
from cerebro_mcp.semantic.bm25 import BM25Doc, BM25Index
from cerebro_mcp.tools.governance.session_state import state
from cerebro_mcp.tools.semantic.data_catalog import catalog_search
from cerebro_mcp.tools.tool_meta import classify_tool
from cerebro_mcp.tools.visualization.mini_apps import get_app_only_tool_names

# ---------------------------------------------------------------------------
# Lazy tool corpus + BM25 index
# ---------------------------------------------------------------------------

_corpus_lock = threading.Lock()
_tool_index: BM25Index | None = None
_tool_docs: dict[str, dict[str, Any]] = {}
_corpus_signature: frozenset[str] | None = None

# Metric Lab tools open an interactive UI — they never *answer* a question, and
# they are dense with the word "metric", so a metric-flavored `find` query would
# otherwise surface them in `top_tools` and nudge the model to open the app
# unprompted. Keep them out of the corpus entirely; the recommended-action
# router steers metric questions to `query_metrics` instead.
_FIND_EXCLUDED_TOOLS: frozenset[str] = frozenset(
    {
        "open_metric_lab",
        "open_metric_lab_from_sql",
        "open_metric_lab_from_metrics",
        "load_metric_lab_metric",
        "update_metric_lab_chart",
    }
)


def _registered_tools(mcp) -> dict[str, Any]:
    """The unfiltered registered-tool map (`name -> Tool`)."""
    manager = getattr(mcp, "_tool_manager", None)
    if manager is None:
        return {}
    return dict(getattr(manager, "_tools", {}) or {})


def _short_summary(description: str) -> str:
    """First non-empty line of a docstring, trimmed."""
    for line in (description or "").strip().splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= 160 else line[:157] + "..."
    return ""


def _call_signature(name: str, parameters: dict[str, Any] | None) -> str:
    """Render an example call like ``tool(required_a=..., required_b=...)``.

    Only required params are shown (keeps the hint short); tools with no
    required params render as ``tool()``.
    """
    params = parameters or {}
    required = list(params.get("required", []) or [])
    if not required:
        return f"{name}()"
    args = ", ".join(f"{p}=..." for p in required[:5])
    return f"{name}({args})"


def _build_tool_corpus(mcp) -> tuple[BM25Index, dict[str, dict[str, Any]]]:
    """Build the find-scoped tool corpus from the unfiltered tool map,
    enriched with `tool_meta` (domain/tags/tier) and EXCLUDING APP_ONLY tools.
    """
    app_only = get_app_only_tool_names()
    docs: list[BM25Doc] = []
    meta_by_name: dict[str, dict[str, Any]] = {}
    for name, tool in _registered_tools(mcp).items():
        if name in app_only or name in _FIND_EXCLUDED_TOOLS:
            continue
        description = getattr(tool, "description", "") or ""
        meta = classify_tool(name, description)
        tags = list(meta.get("tags", []) or [])
        # BM25 blob: name (with underscores split so parts are tokens), the
        # docstring first line, the coarse domain, and the free-text tags.
        blob = " ".join(
            [
                name,
                name.replace("_", " "),
                _short_summary(description),
                str(meta.get("domain", "")),
                " ".join(tags),
            ]
        )
        docs.append(BM25Doc(model_name=name, text=blob))
        meta_by_name[name] = {
            "name": name,
            "summary": _short_summary(description),
            "call": _call_signature(name, getattr(tool, "parameters", None)),
            "domain": meta.get("domain", ""),
            "tier": meta.get("tier", "advanced"),
            "tags": tags,
        }
    return BM25Index(docs), meta_by_name


def _tool_corpus(mcp) -> tuple[BM25Index, dict[str, dict[str, Any]]]:
    """Return the cached tool corpus, (re)building when the tool set changes."""
    global _tool_index, _tool_docs, _corpus_signature
    signature = (
        frozenset(_registered_tools(mcp).keys())
        - get_app_only_tool_names()
        - _FIND_EXCLUDED_TOOLS
    )
    with _corpus_lock:
        if _tool_index is None or signature != _corpus_signature:
            _tool_index, _tool_docs = _build_tool_corpus(mcp)
            _corpus_signature = signature
        return _tool_index, _tool_docs


def _reset_tool_corpus() -> None:
    """Test helper: force a rebuild on the next `_tool_corpus` call."""
    global _tool_index, _tool_docs, _corpus_signature
    with _corpus_lock:
        _tool_index = None
        _tool_docs = {}
        _corpus_signature = None


def _tool_hit(docs: dict[str, dict[str, Any]], name: str) -> dict[str, Any] | None:
    doc = docs.get(name)
    if not doc:
        return None
    return {"name": doc["name"], "summary": doc["summary"], "call": doc["call"]}


def _rank_tools(
    mcp,
    query: str,
    limit: int,
    *,
    pin: list[str] | None = None,
) -> list[dict[str, Any]]:
    """BM25 rank tools, then fold in the same exact/prefix/substring bonus
    pattern `catalog_search` uses so canonical names rise to the top.

    ``pin`` names (the routed next tool + mode follow-ups) are placed FIRST so
    `find` always surfaces actionable tools even when the query is pure domain
    vocabulary (e.g. "transaction count by sector") with no tool-name overlap.
    """
    index, docs = _tool_corpus(mcp)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in pin or []:
        if name in seen:
            continue
        hit = _tool_hit(docs, name)
        if hit is not None:
            out.append(hit)
            seen.add(name)

    q = (query or "").strip()
    if q:
        ql = q.lower()
        scored: dict[str, float] = {}
        for name, sc in index.search(q, top_k=len(docs) or 1):
            scored[name] = float(sc)
        for name in docs:
            nl = name.lower()
            bonus = 0.0
            if nl == ql:
                bonus += 6.0
            elif nl.startswith(ql) or ql in nl:
                bonus += 3.5 if nl.startswith(ql) else 1.5
            # Any query token that is a whole word-part of the tool name.
            for tok in (t for t in ql.replace("_", " ").split() if len(t) > 2):
                if tok in nl:
                    bonus += 1.0
            if bonus:
                scored[name] = scored.get(name, 0.0) + bonus
        # Gentle core-tier boost so everyday entry points lead their long-tail
        # siblings on ties.
        for name in list(scored):
            if docs.get(name, {}).get("tier") == "core":
                scored[name] += 0.75
        ordered = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
        for name, _sc in ordered:
            if len(out) >= max(1, limit):
                break
            if name in seen:
                continue
            hit = _tool_hit(docs, name)
            if hit is not None:
                out.append(hit)
                seen.add(name)
    return out[: max(1, limit)]


# ---------------------------------------------------------------------------
# Recommended-action builder
# ---------------------------------------------------------------------------


def _infer_mode(query: str) -> str:
    """`auto` intent inference — conservative: default to answer unless the
    query clearly asks for a chart/report/visual artifact."""
    ql = (query or "").lower()
    report_words = ("report", "dashboard", "deep dive", "deep-dive", "write up", "write-up")
    chart_words = ("chart", "plot", "graph", "visualize", "visualise", "visual", "bar chart", "line chart")
    if any(w in ql for w in report_words):
        return "report"
    if any(w in ql for w in chart_words):
        return "chart"
    return "answer"


def _recommended_action(
    *,
    query: str,
    mode: str,
    route: str,
    recommended_metrics: list[str],
    recommended_dimensions: list[str],
) -> dict[str, Any]:
    """Build the mode-aware next-action hint.

    answer/auto → `query_metrics` DIRECTLY (no preflight).
    chart/report → the preflight -> chart/report path so the hard gate is not
    skipped.
    """
    metrics = list(recommended_metrics[:3])
    dims = list(recommended_dimensions[:3])
    has_metrics = route in ("semantic_ready", "hybrid_ready") and bool(metrics)

    if mode in ("chart", "report"):
        args: dict[str, Any] = {"query": query, "mode": mode}
        return {
            "tool": "preflight_analytics_request",
            "args": args,
            "call": f'preflight_analytics_request(query={query!r}, mode="{mode}")',
            "why": (
                f"{mode} mode needs the semantic preflight gate before "
                f"generate_{'report' if mode == 'report' else 'chart'}. "
                "Run preflight, then the recommended chart/report tool."
            ),
        }

    # answer / auto
    if has_metrics:
        args = {"metrics": metrics}
        if dims:
            args["dimensions"] = dims
        call = f"query_metrics(metrics={metrics}"
        call += f", dimensions={dims})" if dims else ")"
        return {
            "tool": "query_metrics",
            "args": args,
            "call": call,
            "why": (
                "Approved semantic metric covers this — answer directly with "
                "query_metrics (no preflight needed in answer mode)."
            ),
        }
    return {
        "tool": "discover_models",
        "args": {"query": query, "detail_top_n": 5},
        "call": f"discover_models(query={query!r}, detail_top_n=5)",
        "why": (
            "No approved metric matched. Use raw discovery: "
            "discover_models -> describe_table -> execute_query."
        ),
    }


def _top_metrics(routing: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """Compact metric hits from the routing accepted list (executable=True)."""
    out: list[dict[str, Any]] = []
    for _score, name, metric in routing.get("accepted", [])[: max(1, limit)]:
        out.append(
            {
                "name": name,
                "label": metric.get("label", "") or name,
                "executable": True,
            }
        )
    return out


def _top_models(query: str, limit: int) -> list[dict[str, Any]]:
    """Model hits via the shared catalog index (entity_types=['model'] only —
    ENTITY_TYPES itself is untouched)."""
    try:
        result = catalog_search(query=query, entity_types=["model"], limit=max(1, limit))
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for hit in result.get("hits", [])[: max(1, limit)]:
        out.append(
            {
                "name": hit.get("name", ""),
                "title": hit.get("title", ""),
                "module": hit.get("module", ""),
                "tier": hit.get("tier", ""),
                "description": (hit.get("description", "") or "")[:160],
            }
        )
    return out


def build_find_response(mcp, query: str, mode: str, limit: int) -> dict[str, Any]:
    """Pure builder for the `find` payload (no state writes) — importable by
    the slim preflight path and tests."""
    from cerebro_mcp.tools.semantic.semantic import _route

    effective_limit = max(1, min(int(limit or 8), 25))
    normalized_mode = (mode or "auto").strip().lower()
    if normalized_mode not in ("answer", "chart", "report", "auto"):
        normalized_mode = "auto"
    route_mode = _infer_mode(query) if normalized_mode == "auto" else normalized_mode

    routing = _route(query, route_mode)
    route = routing["route"]
    recommended_metrics = routing["recommended_metrics"]
    recommended_dimensions = routing["recommended_dimensions"]

    action = _recommended_action(
        query=query,
        mode=route_mode,
        route=route,
        recommended_metrics=recommended_metrics,
        recommended_dimensions=recommended_dimensions,
    )

    # Pin the actionable tools FIRST so `find` always surfaces the next tool +
    # mode-appropriate follow-ups even when the query is pure domain vocabulary.
    pin = [action["tool"]]
    if route_mode in ("chart", "report"):
        pin += ["query_metrics", routing["next_tool"], "generate_metric_charts"]
    elif route in ("semantic_ready", "hybrid_ready"):
        pin += ["query_metrics", "get_metric_details", "explain_metric_query"]
    else:
        pin += ["discover_models", "describe_table", "execute_query"]

    return {
        "query": query,
        "mode": normalized_mode,
        "route": route,
        "top_tools": _rank_tools(mcp, query, effective_limit, pin=pin),
        "top_metrics": _top_metrics(routing, min(effective_limit, 5)),
        "top_models": _top_models(query, effective_limit),
        "recommended_action": action,
        "_routing": routing,  # internal — the tool strips this before returning
    }


def register_find_tool(mcp) -> None:
    """Register the `find` tool. MUST be called LAST (after every other tool
    registration) so the lazy tool corpus can see the full surface on first
    call."""
    if not settings.SEMANTIC_ENABLED:
        return

    @mcp.tool()
    def find(query: str, mode: str = "auto", limit: int = 8) -> dict[str, Any]:
        """Single front door: one call routes a request to the right tools,
        metrics, and models, with a pre-filled next action.

        Use this FIRST for almost any analytical question — it replaces the
        ToolSearch/preflight discovery dance. For a plain answer (default),
        it points straight at `query_metrics` when an approved metric covers
        the question; for `mode="chart"`/`"report"` it routes through
        `preflight_analytics_request` so the chart/report gate is respected.

        Args:
            query: Natural-language request (e.g. "gnosis app active users last week").
            mode: "answer" (default intent), "chart", "report", or "auto"
                  (infer intent — defaults to answer unless the query clearly
                  asks for a chart/report).
            limit: Max hits per section (1-25, default 8).

        Returns:
            ``{query, mode, route, top_tools, top_metrics, top_models,
            recommended_action}``.
        """
        try:
            state.record_semantic_tool_call("find")
        except Exception:
            pass
        payload = build_find_response(mcp, query, mode, limit)
        routing = payload.pop("_routing", {})
        try:
            state.record_semantic_find(
                route=payload["route"],
                mode=payload["mode"],
                recommended_metrics=routing.get("recommended_metrics", []),
            )
        except Exception:
            pass
        return payload

    return find
