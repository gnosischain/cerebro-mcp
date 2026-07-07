"""Process-global, thread-safe session state tracker.

Tracks tool calls across the analysis workflow and enforces preconditions
on generate_chart and generate_report. All mutations are guarded by a
threading.Lock so the singleton is safe under concurrent tool calls.
"""

import hashlib
import re
import threading
import time
from dataclasses import dataclass, field

from cerebro_mcp.config import settings
from cerebro_mcp.runtime.observability import (
    observe_discovered_model_coverage,
    observe_gate_evaluation_seconds,
    observe_quality_gate,
)
from cerebro_mcp.tools.analytics.sql_heuristics import (
    DEFAULT_STOCK_COLUMNS,
    HeuristicViolation,
    evaluate_all,
)

_STATISTICAL_RE = re.compile(
    r"quantile|quantiles|stddev|corr|covar|simpleLinearRegression"
    r"|entropy|varPop|median|percentile",
    re.IGNORECASE,
)

_CORRELATION_RE = re.compile(
    r"\bcorr\s*\(|\bcovar(?:Pop|Samp)?\s*\(|\bsimpleLinearRegression\s*\(",
    re.IGNORECASE,
)
_QUALIFIED_TABLE_RE = re.compile(
    r"\b(?:from|join)\s+`?(?:[a-zA-Z0-9_]+)`?\.`?([a-zA-Z0-9_]+)`?",
    re.IGNORECASE,
)


def _format_chart_gate_reason(missing: list[str]) -> str:
    """Render all unmet chart preconditions as one actionable message.

    A single gap is returned verbatim (preserves the original one-line wording
    and any downstream substring checks). Multiple gaps are bundled under one
    header so the caller can satisfy them in a single follow-up batch instead of
    one tool round-trip per gap.
    """
    if len(missing) == 1:
        return missing[0]
    bullets = "\n".join(f"- {item}" for item in missing)
    return (
        "Chart prerequisites not yet met — complete all of the following, "
        "then retry in one step:\n" + bullets
    )


@dataclass
class SessionState:
    # Discovery tracking
    search_models_count: int = 0
    explored_models: set[str] = field(default_factory=set)
    explored_tables: set[str] = field(default_factory=set)
    verified_query_surfaces: set[str] = field(default_factory=set)
    # Models surfaced by search_models / discover_models / discover_metrics
    # but not (yet) explicitly explored or queried. The discovered-model
    # coverage gate compares this to verified_query_surfaces + explored_models.
    discovered_models: set[str] = field(default_factory=set)
    # Models the agent explicitly excludes from a report — populated via
    # `record_model_exclusion(name, reason)` so the coverage gate can ignore them.
    excluded_models: set[str] = field(default_factory=set)

    # Execution tracking
    execute_query_count: int = 0
    generate_chart_count: int = 0
    statistical_query_count: int = 0
    correlation_query_count: int = 0
    chart_types_generated: set[str] = field(default_factory=set)

    # Semantic routing tracking
    semantic_tools_available: bool = False
    semantic_tool_calls: int = 0
    semantic_preflight_ran: bool = False
    # `find` router routed the request (answer/auto mode discovery). SEPARATE
    # from `semantic_preflight_ran` on purpose: an answer-mode `find` unblocks
    # raw discovery for free but must NOT open the chart/report hard gates,
    # which stay reserved for a real `preflight_analytics_request`.
    semantic_find_ran: bool = False
    semantic_find_route: str = ""
    semantic_route_last: str = ""
    semantic_mode_last: str = ""
    semantic_execution_attempted: bool = False
    semantic_fallback_recorded: bool = False
    semantic_fallback_reason: str = ""
    semantic_path_used: str = "none"
    semantic_preflight_cache: dict[str, dict[str, object]] = field(default_factory=dict)
    analysis_path: str = "undecided"

    # Thread safety
    lock: threading.Lock = field(default_factory=threading.Lock)

    # ── Internal helpers ────────────────────────────────────────────

    def _record_path_unlocked(self, path: str) -> None:
        if path == "semantic":
            if self.semantic_path_used in ("none", "semantic"):
                self.semantic_path_used = "semantic"
            elif self.semantic_path_used == "raw_only":
                self.semantic_path_used = "mixed"
            return

        if path == "raw":
            if self.semantic_path_used in ("none", "raw_only"):
                self.semantic_path_used = "raw_only"
            elif self.semantic_path_used == "semantic":
                self.semantic_path_used = "mixed"

    def _preflight_cache_key(self, query: str, mode: str) -> str:
        return f"{query.strip().lower()}::{mode.strip().lower()}"

    def _record_verified_query_surface_unlocked(self, sql: str) -> None:
        matches = {
            match
            for match in _QUALIFIED_TABLE_RE.findall(sql or "")
            if match
        }
        if matches:
            self.verified_query_surfaces.update(matches)
            return

        normalized_sql = " ".join((sql or "").split())
        if not normalized_sql:
            return
        fallback_key = hashlib.md5(normalized_sql.encode("utf-8")).hexdigest()[:12]
        self.verified_query_surfaces.add(f"query::{fallback_key}")

    def _min_models_detailed_for_tier_unlocked(self) -> int:
        """Lineage-depth requirement scaled to the active reporting tier.

        Light tiers (`mode="chart"` / `mode="answer"`, i.e. single_chart /
        lite_report) legitimately chart from a single known model, so they need
        only `MIN_MODELS_DETAILED_LITE` lineage lookups. The full `report` tier
        and the non-semantic path (where `semantic_mode_last` is unset) keep the
        stricter `MIN_MODELS_DETAILED`.
        """
        if self.semantic_mode_last in {"chart", "answer"}:
            return settings.MIN_MODELS_DETAILED_LITE
        return settings.MIN_MODELS_DETAILED

    def _collect_common_chart_gaps_unlocked(self) -> list[str]:
        """Return every unmet discovery/lineage/schema precondition, together.

        Unlike a first-failure early return, this reports ALL remaining gaps in
        one pass so the caller can satisfy them in a single follow-up batch
        instead of one tool round-trip per gap.
        """
        gaps: list[str] = []

        if self.search_models_count == 0:
            gaps.append(
                "Discovery: call `search_models`, `discover_models`, or "
                "`discover_metrics` to find the relevant public data surface."
            )

        min_detailed = self._min_models_detailed_for_tier_unlocked()
        if len(self.explored_models) < min_detailed:
            gaps.append(
                f"Lineage: call `get_model_details` or `get_metric_details` "
                f"for at least {min_detailed} item(s) (currently explored: "
                f"{len(self.explored_models)}). `describe_table` alone does not "
                f"explain lineage or semantic coverage."
            )

        min_verified = settings.MIN_TABLES_VERIFIED
        verified_surfaces = set(self.explored_tables)
        verified_surfaces.update(self.verified_query_surfaces)
        if len(verified_surfaces) < min_verified:
            gaps.append(
                f"Schema: verify at least {min_verified} table(s) via "
                f"`describe_table`, a successful `execute_query`, or a semantic "
                f"query (currently verified: {len(verified_surfaces)})."
            )

        return gaps

    # ── Record methods ──────────────────────────────────────────────

    def set_semantic_tools_available(self, available: bool) -> None:
        with self.lock:
            self.semantic_tools_available = available

    def record_search_models(
        self,
        query: str,
        results_count: int,
        *,
        source: str = "raw",
        model_names: list[str] | None = None,
    ) -> None:
        with self.lock:
            self.search_models_count += 1
            if model_names:
                self.discovered_models.update(model_names)
            if source == "raw":
                self._record_path_unlocked("raw")

    def record_model_exclusion(self, model_name: str, reason: str = "") -> None:
        """Mark a discovered model as deliberately excluded from the report.

        Suppresses the discovered-model-coverage gate for this model.
        The reason is recorded in the session trace for auditability.
        """
        with self.lock:
            self.excluded_models.add(model_name)

    def record_get_model_details(
        self,
        model_name: str,
        *,
        source: str = "raw",
    ) -> None:
        with self.lock:
            self.explored_models.add(model_name)
            if source == "raw":
                self._record_path_unlocked("raw")

    def record_describe_table(
        self,
        table: str,
        *,
        source: str = "raw",
    ) -> None:
        with self.lock:
            self.explored_tables.add(table)
            if source == "raw":
                self._record_path_unlocked("raw")

    def record_execute_query(self, sql: str, *, source: str = "raw") -> None:
        with self.lock:
            self.execute_query_count += 1
            if self.is_statistical_query(sql):
                self.statistical_query_count += 1
            if self._is_correlation_query(sql):
                self.correlation_query_count += 1
            self._record_verified_query_surface_unlocked(sql)
            self._record_path_unlocked("semantic" if source == "semantic" else "raw")

    def record_generate_chart(
        self,
        chart_type: str,
        sql: str,
        series_field: str = "",
        *,
        source: str = "raw",
    ) -> None:
        with self.lock:
            self.generate_chart_count += 1
            self.chart_types_generated.add(chart_type)
            self._record_path_unlocked("semantic" if source == "semantic" else "raw")

    def record_semantic_preflight(
        self,
        *,
        route: str,
        mode: str,
        fallback_reason: str = "",
    ) -> None:
        with self.lock:
            self.semantic_tools_available = True
            self.semantic_preflight_ran = True
            self.semantic_route_last = route
            self.semantic_mode_last = mode
            self.semantic_fallback_reason = fallback_reason
            self.semantic_fallback_recorded = route == "semantic_coverage_gap"
            if route == "semantic_ready":
                self.analysis_path = "semantic_only"
            elif route == "hybrid_ready":
                self.analysis_path = "hybrid"
            elif route in ("semantic_coverage_gap", "semantic_disabled", "semantic_unavailable"):
                self.analysis_path = "raw_only"

    def record_semantic_find(
        self,
        *,
        route: str,
        mode: str,
        recommended_metrics: list[str] | None = None,
    ) -> None:
        """Record that the `find` router routed a request.

        Mirrors :meth:`record_semantic_preflight` for `semantic_route_last` /
        `semantic_mode_last` / `analysis_path` bookkeeping BUT sets
        `semantic_find_ran` instead of `semantic_preflight_ran`. This lets an
        answer-mode `find` satisfy the discovery nudge (`_semantic_discovery_gate`)
        for free while the chart/report hard gates keep requiring the real
        preflight flag.
        """
        with self.lock:
            self.semantic_tools_available = True
            self.semantic_find_ran = True
            self.semantic_find_route = route
            self.semantic_route_last = route
            self.semantic_mode_last = mode
            if route == "semantic_ready":
                self.analysis_path = "semantic_only"
            elif route == "hybrid_ready":
                self.analysis_path = "hybrid"
            elif route in ("semantic_coverage_gap", "semantic_disabled", "semantic_unavailable"):
                self.analysis_path = "raw_only"

    def get_cached_semantic_preflight(
        self,
        *,
        query: str,
        mode: str,
    ) -> dict[str, object] | None:
        with self.lock:
            cached = self.semantic_preflight_cache.get(
                self._preflight_cache_key(query, mode)
            )
            return dict(cached) if cached else None

    def cache_semantic_preflight(
        self,
        *,
        query: str,
        mode: str,
        result: dict[str, object],
    ) -> None:
        with self.lock:
            self.semantic_preflight_cache[
                self._preflight_cache_key(query, mode)
            ] = dict(result)

    def record_semantic_tool_call(
        self,
        tool_name: str,
        *,
        execution: bool = False,
    ) -> None:
        with self.lock:
            self.semantic_tools_available = True
            self.semantic_tool_calls += 1
            if execution:
                self.semantic_execution_attempted = True
                self._record_path_unlocked("semantic")

    def record_semantic_fallback(self, reason: str) -> None:
        with self.lock:
            self.semantic_tools_available = True
            self.semantic_fallback_recorded = True
            self.semantic_fallback_reason = reason

    # ── Statistical helpers (advisory only) ─────────────────────────

    def is_statistical_query(self, sql: str) -> bool:
        """Check if SQL uses statistical functions. Soft signal only."""
        return bool(_STATISTICAL_RE.search(sql))

    def _is_correlation_query(self, sql: str) -> bool:
        """Check if SQL uses correlation/regression functions."""
        return bool(_CORRELATION_RE.search(sql))

    def suggest_statistical_functions(self, sql: str) -> str | None:
        """Return a gentle nudge if the query lacks statistical functions."""
        if not self.is_statistical_query(sql):
            return (
                "Consider using `quantiles`, `stddev`, or `corr` for "
                "richer, more robust analysis instead of basic averages."
            )
        return None

    # ── Precondition checks ─────────────────────────────────────────

    def check_chart_preconditions(self, *, raw_path: bool = True) -> tuple[bool, str]:
        """Gate for chart generation. Returns (passed, reason).

        Reports EVERY unmet precondition in a single combined message (preflight
        + discovery + lineage + schema) so the caller satisfies them in one
        retry instead of one tool round-trip per gate. Route-redirect errors
        (approved semantic coverage / wrong route) stay standalone: they tell
        the caller to switch tools, not to gather more context.
        """
        if not settings.ENFORCE_CHART_PRECONDITIONS:
            return True, ""

        with self.lock:
            missing: list[str] = []

            if settings.SEMANTIC_ENABLED:
                # `find` and `preflight_analytics_request` record identical
                # route/mode/analysis_path data, so EITHER satisfies the chart
                # gate — no need to call both. (Reports remain stricter: the
                # report gate requires an explicit preflight in mode="report".)
                if not (self.semantic_preflight_ran or self.semantic_find_ran):
                    missing.append(
                        "Semantic preflight required: call "
                        "`find(query, mode=\"chart\")` or "
                        "`preflight_analytics_request(query, mode=\"chart\")` "
                        "before charting when semantic is enabled."
                    )
                else:
                    # Route redirects only make sense once routing has run.
                    if raw_path:
                        # In hybrid mode, allow raw charts alongside semantic
                        if self.analysis_path == "hybrid":
                            pass  # raw allowed in hybrid
                        elif self.semantic_route_last == "semantic_ready" and not self.semantic_execution_attempted:
                            return False, (
                                "Approved semantic coverage already exists for this "
                                "request. Use `quick_metric_chart`, "
                                "`generate_metric_charts`, `query_metrics`, or "
                                "`explain_metric_query` before raw charting."
                            )

                    if not raw_path and self.semantic_route_last not in ("semantic_ready", "hybrid_ready"):
                        fallback = (
                            f" Fallback reason: {self.semantic_fallback_reason}."
                            if self.semantic_fallback_reason
                            else ""
                        )
                        return False, (
                            "Semantic charting requires a `semantic_ready` or "
                            "`hybrid_ready` route from "
                            f"`preflight_analytics_request`. Current route: "
                            f"`{self.semantic_route_last or 'unknown'}`.{fallback}"
                        )

            missing.extend(self._collect_common_chart_gaps_unlocked())

            if missing:
                return False, _format_chart_gate_reason(missing)
            return True, ""

    def check_report_preconditions(
        self,
        chart_registry: dict,
    ) -> tuple[bool, str, list[str]]:
        """Gate for generate_report. Returns (passed, reason, warnings)."""
        if not settings.ENFORCE_CHART_PRECONDITIONS:
            return True, "", []

        warnings: list[str] = []

        with self.lock:
            if settings.SEMANTIC_ENABLED:
                if not self.semantic_preflight_ran:
                    return False, (
                        "Semantic preflight required: call "
                        "`preflight_analytics_request(query, mode=\"report\")` "
                        "before generating a report when semantic is enabled."
                    ), []

                if self.analysis_path == "semantic_only" and not self.semantic_execution_attempted:
                    return False, (
                        "Approved semantic coverage already exists for this "
                        "request. Use `query_metrics`, `quick_metric_chart`, "
                        "or `generate_metric_charts` before generating a "
                        "report."
                    ), []

                if self.semantic_mode_last in {"answer", "chart"}:
                    if settings.REPORT_REQUIRES_EXPLICIT_MODE:
                        # A report artifact is only produced when the user
                        # EXPLICITLY asked for one (preflight mode="report").
                        # A request routed as a chart or a plain answer presents
                        # its chart(s) inline and stops. Enforced here because
                        # prose guidance alone did not hold — the model would
                        # read "don't build a report" and build one anyway.
                        return False, (
                            "This request was not routed as a report "
                            f"(mode=\"{self.semantic_mode_last}\"). The chart(s) "
                            "you generated ARE the deliverable — present them in "
                            "your reply and STOP. Do not build a report for a "
                            "plain 'show me / plot X' or data question. Only if "
                            "the user EXPLICITLY asked for a report, dashboard, "
                            "or written analysis, re-run "
                            "`preflight_analytics_request(query, mode=\"report\")` "
                            "first, then satisfy the full report gates."
                        ), []
                    # Legacy lite-report bypass (toggle off): answer/chart mode
                    # renders a lightweight report with >= 1 chart.
                    if len(chart_registry) < 1:
                        return False, (
                            "At least one chart is required before rendering "
                            "a lightweight visual answer."
                        ), []
                    return True, "", []

            min_charts = settings.MIN_CHARTS_FOR_REPORT
            if len(chart_registry) < min_charts:
                return False, (
                    f"Insufficient charts: Generated {len(chart_registry)} "
                    f"chart(s), but the minimum required for a report is "
                    f"{min_charts}."
                ), []

            if settings.REQUIRE_CHART_DIVERSITY:
                has_trend = any(
                    v.get("chart_type") in ("line", "area")
                    for v in chart_registry.values()
                )
                has_breakdown = any(
                    v.get("chart_type") in ("bar", "pie")
                    for v in chart_registry.values()
                )
                if not has_trend and not has_breakdown:
                    return False, (
                        "Chart diversity lacking: Report must include at "
                        "least one trend chart (line/area) or one breakdown "
                        "chart (bar/pie)."
                    ), []

            min_queries = settings.MIN_EXPLORATORY_QUERIES
            if self.execute_query_count < min_queries:
                return False, (
                    f"Insufficient exploration: Run at least {min_queries} "
                    f"exploratory queries (EDA, distribution checks, "
                    f"dimensional queries) before generating a report. "
                    f"(Currently run: {self.execute_query_count})."
                ), []

            min_stats = settings.MIN_STATISTICAL_QUERIES
            if self.statistical_query_count < min_stats:
                warnings.append(
                    f"No statistical queries detected (quantiles, stddev, "
                    f"corr, etc.). Consider running EDA with statistical "
                    f"functions for more robust analysis."
                )

            min_corr = settings.MIN_CORRELATION_QUERIES
            if len(chart_registry) >= 3 and self.correlation_query_count < min_corr:
                warnings.append(
                    f"No correlation/regression queries detected. Consider "
                    f"using corr(), covarPop(), or simpleLinearRegression() "
                    f"to analyze relationships between metrics."
                )

            if settings.REQUIRE_DIMENSIONAL_BREAKDOWN:
                has_dimensional = any(
                    v.get("series_field")
                    or v.get("chart_type") in (
                        "pie", "treemap", "heatmap", "sankey",
                    )
                    for v in chart_registry.values()
                )
                if not has_dimensional:
                    return False, (
                        "No dimensional breakdown: At least one chart must "
                        "use series_field to show data split by a dimension "
                        "(token, action type, segment, etc.), or use a "
                        "pie/treemap/heatmap/sankey chart type."
                    ), []

            if settings.REQUIRE_RELATIONAL_CHART:
                has_relational = any(
                    v.get("chart_type") in ("scatter", "heatmap")
                    for v in chart_registry.values()
                )
                has_correlation = self.correlation_query_count >= 1
                if not has_relational and not has_correlation:
                    return False, (
                        "No relational analysis: At least one scatter/"
                        "heatmap chart OR one correlation query (corr(), "
                        "covarPop(), simpleLinearRegression()) is required "
                        "for multi-dimensional analysis."
                    ), []

            if self.execute_query_count < 5 and self.generate_chart_count > 0:
                warnings.append(
                    "Only a few exploratory queries were run before "
                    "charting. Consider deeper EDA for more robust analysis."
                )

            if "scatter" not in self.chart_types_generated and self.generate_chart_count >= 3:
                warnings.append(
                    "No scatter chart generated. Consider adding a scatter "
                    "plot to visualize strong correlations (|r| > 0.5)."
                )

            # ── Quality-discipline gates ───────────────────────────
            # SQL-text heuristics over each chart's SQL. Each gate is
            # individually toggleable via settings; metadata-based override
            # works when the chart's title/description acknowledges the
            # antipattern (see _shared_quality_rules.md).
            heuristic_enabled = {
                "stock_flow": settings.ENFORCE_STOCK_FLOW_DISCIPLINE,
                "residual_bucket": settings.ENFORCE_RESIDUAL_BUCKET_DISCLOSURE,
                "stationarity": settings.ENFORCE_STATIONARITY_ON_CORRELATIONS,
                "aggregator_dedup": settings.ENFORCE_AGGREGATOR_VOLUME_DEDUP,
            }
            if any(heuristic_enabled.values()):
                stock_cols = frozenset(DEFAULT_STOCK_COLUMNS).union(
                    settings.STOCK_MEASURE_COLUMNS_EXTRA or []
                )
                violations: list[HeuristicViolation] = []
                heuristics_started = time.perf_counter()
                for chart_id, chart in chart_registry.items():
                    chart_sql = chart.get("sql", "") or ""
                    if not chart_sql:
                        continue
                    chart_meta = {
                        "title": chart.get("title", ""),
                        "subtitle": chart.get("subtitle", ""),
                        "description": chart.get("description", ""),
                        "override_reason": chart.get("override_reason", ""),
                    }
                    violations.extend(evaluate_all(
                        chart_id=chart_id,
                        sql=chart_sql,
                        chart_metadata=chart_meta,
                        enabled=heuristic_enabled,
                        stock_columns=stock_cols,
                    ))
                observe_gate_evaluation_seconds(
                    "sql_heuristics_all",
                    time.perf_counter() - heuristics_started,
                )

                # Telemetry: count pass/fail per rule across the chart set.
                # A rule "passes" for a chart when no violation is recorded.
                violations_by_rule: dict[str, int] = {}
                for v in violations:
                    violations_by_rule[v.rule] = (
                        violations_by_rule.get(v.rule, 0) + 1
                    )
                rule_to_setting = {
                    "stock_flow_discipline": "stock_flow",
                    "residual_bucket_disclosure": "residual_bucket",
                    "stationarity_on_correlations": "stationarity",
                    "aggregator_volume_dedup": "aggregator_dedup",
                }
                for rule_name, settings_key in rule_to_setting.items():
                    if not heuristic_enabled.get(settings_key, False):
                        continue
                    failures = violations_by_rule.get(rule_name, 0)
                    passes = max(0, len(chart_registry) - failures)
                    for _ in range(passes):
                        observe_quality_gate(rule_name, "pass")
                    for _ in range(failures):
                        observe_quality_gate(rule_name, "fail")

                if violations:
                    detail_lines = [
                        f"- {v.chart_id} [{v.rule}]: {v.message}"
                        for v in violations
                    ]
                    return False, (
                        "Quality discipline violation(s) on "
                        f"{len(violations)} chart(s). Either rewrite the SQL "
                        "to follow the rules in `_shared_quality_rules.md`, "
                        "or attach an explicit `override_reason` / "
                        "`description` to the chart that acknowledges the "
                        "antipattern with a stated reason.\n\n"
                        + "\n".join(detail_lines)
                    ), []

            # Discovered-model coverage gate: every model surfaced by
            # search_models / discover_models that is not explored, queried,
            # or explicitly excluded counts as an unused discovery.
            if settings.ENFORCE_DISCOVERED_MODEL_COVERAGE and self.discovered_models:
                coverage_started = time.perf_counter()
                covered = (
                    self.explored_models
                    | self.verified_query_surfaces
                    | self.excluded_models
                )
                uncovered = self.discovered_models - covered
                observe_gate_evaluation_seconds(
                    "discovered_model_coverage",
                    time.perf_counter() - coverage_started,
                )
                if uncovered:
                    observe_discovered_model_coverage("fail")
                    observe_quality_gate("discovered_model_coverage", "fail")
                    sample = ", ".join(sorted(uncovered)[:10])
                    extra = (
                        f" (and {len(uncovered) - 10} more)"
                        if len(uncovered) > 10
                        else ""
                    )
                    return False, (
                        f"Discovered-but-unused models: {len(uncovered)} "
                        f"model(s) returned by `search_models` / "
                        f"`discover_models` were never queried with "
                        f"`execute_query` / `start_query`, never explored "
                        f"with `get_model_details`, and were not marked as "
                        f"excluded via `record_model_exclusion`. "
                        f"Either query them, get details on them, or call "
                        f"`record_model_exclusion(name, reason)` for each "
                        f"one before generating the report. "
                        f"Uncovered: {sample}{extra}."
                    ), []
                else:
                    observe_discovered_model_coverage("pass")
                    observe_quality_gate("discovered_model_coverage", "pass")

        return True, "", warnings

    # ── Reporting helpers ───────────────────────────────────────────

    def semantic_summary(self) -> dict[str, object]:
        with self.lock:
            return {
                "semantic_tools_available": self.semantic_tools_available,
                "semantic_tool_calls": self.semantic_tool_calls,
                "semantic_path_used": self.semantic_path_used,
                "semantic_route_last": self.semantic_route_last,
                "semantic_mode_last": self.semantic_mode_last,
                "semantic_preflight_ran": self.semantic_preflight_ran,
                "semantic_find_ran": self.semantic_find_ran,
                "semantic_find_route": self.semantic_find_route,
                "semantic_execution_attempted": self.semantic_execution_attempted,
                "semantic_fallback_recorded": self.semantic_fallback_recorded,
                "semantic_fallback_reason": self.semantic_fallback_reason,
                "semantic_preflight_cache_size": len(self.semantic_preflight_cache),
                "verified_query_surfaces": len(self.verified_query_surfaces),
                "analysis_path": self.analysis_path,
            }

    # ── Reset ───────────────────────────────────────────────────────

    def begin_analysis_cycle(self) -> None:
        """Reset the per-report accumulators at the start of an analysis cycle.

        Called by each ``preflight_analytics_request``. Historically this wiped
        EVERYTHING, including the discovery/lineage/schema evidence the chart
        gate reads — which created a redo loop: the model would discover and
        explore, then call preflight (the only way to set ``semantic_preflight_ran``
        and open the chart gate), and that preflight erased the discovery, so the
        chart gate then bounced with discovery/lineage/schema all at zero.

        Fix: preserve the "data-surface evidence" the chart gate reads
        (``search_models_count`` / ``explored_models`` / ``explored_tables`` /
        ``verified_query_surfaces`` / ``execute_query_count``) so a preflight
        that follows discovery does not throw it away. Still reset the per-report
        accumulators that must be scoped to the current question: the
        discovered-model coverage set and the chart / statistical / correlation
        counters. The full ``reset()`` after a successful ``generate_report``
        still clears everything, so isolation at the report boundary is
        preserved. The semantic preflight cache is untouched so repeated
        identical preflights still hit the cache.
        """
        with self.lock:
            # Coverage-gate set — scoped to the current question so a stale
            # discovered model can't leak into a new report's coverage gate.
            self.discovered_models.clear()
            self.excluded_models.clear()
            # Report-quality accumulators — fresh per report.
            self.generate_chart_count = 0
            self.statistical_query_count = 0
            self.correlation_query_count = 0
            self.chart_types_generated.clear()
            # PRESERVED (do NOT clear): search_models_count, explored_models,
            # explored_tables, verified_query_surfaces, execute_query_count —
            # the data-surface evidence the chart gate reads. Wiping these here
            # is what created the discover -> preflight -> 0/0/0 redo loop.

    def reset(self) -> None:
        """Clear all tracked state. Called after successful generate_report."""
        with self.lock:
            self.search_models_count = 0
            self.explored_models.clear()
            self.explored_tables.clear()
            self.verified_query_surfaces.clear()
            self.discovered_models.clear()
            self.excluded_models.clear()
            self.execute_query_count = 0
            self.generate_chart_count = 0
            self.statistical_query_count = 0
            self.correlation_query_count = 0
            self.chart_types_generated.clear()
            self.semantic_tools_available = False
            self.semantic_tool_calls = 0
            self.semantic_preflight_ran = False
            self.semantic_find_ran = False
            self.semantic_find_route = ""
            self.semantic_route_last = ""
            self.semantic_mode_last = ""
            self.semantic_execution_attempted = False
            self.semantic_fallback_recorded = False
            self.semantic_fallback_reason = ""
            self.semantic_path_used = "none"
            self.semantic_preflight_cache.clear()
            self.analysis_path = "undecided"


# Global singleton
state = SessionState()
