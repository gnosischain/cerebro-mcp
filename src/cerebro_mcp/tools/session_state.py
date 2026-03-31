"""Process-global, thread-safe session state tracker.

Tracks tool calls across the analysis workflow and enforces preconditions
on generate_chart and generate_report. All mutations are guarded by a
threading.Lock so the singleton is safe under concurrent tool calls.
"""

import hashlib
import re
import threading
from dataclasses import dataclass, field

from cerebro_mcp.config import settings

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


@dataclass
class SessionState:
    # Discovery tracking
    search_models_count: int = 0
    explored_models: set[str] = field(default_factory=set)
    explored_tables: set[str] = field(default_factory=set)
    verified_query_surfaces: set[str] = field(default_factory=set)

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

    def _check_common_chart_preconditions_unlocked(self) -> tuple[bool, str]:
        if self.search_models_count == 0:
            return False, (
                "Discovery skipped: You must call `search_models`, "
                "`discover_models`, or `discover_metrics` first to find the "
                "relevant public data surface."
            )

        min_detailed = settings.MIN_MODELS_DETAILED
        if len(self.explored_models) < min_detailed:
            return False, (
                f"Insufficient lineage exploration: You must call "
                f"`get_model_details` or `get_metric_details` for at least "
                f"{min_detailed} items. (Currently explored: "
                f"{len(self.explored_models)}). `describe_table` alone is "
                f"not sufficient because it does not explain lineage or "
                f"semantic coverage."
            )

        min_verified = settings.MIN_TABLES_VERIFIED
        verified_surfaces = set(self.explored_tables)
        verified_surfaces.update(self.verified_query_surfaces)
        if len(verified_surfaces) < min_verified:
            return False, (
                f"Insufficient schema verification: You must verify at least "
                f"{min_verified} table(s) via `describe_table`, a successful "
                f"`execute_query`, or execute a semantic query first. "
                f"(Currently verified: {len(verified_surfaces)})."
            )

        return True, ""

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
    ) -> None:
        with self.lock:
            self.search_models_count += 1
            if source == "raw":
                self._record_path_unlocked("raw")

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
        """Gate for chart generation. Returns (passed, reason)."""
        if not settings.ENFORCE_CHART_PRECONDITIONS:
            return True, ""

        with self.lock:
            if settings.SEMANTIC_ENABLED:
                if not self.semantic_preflight_ran:
                    return False, (
                        "Semantic preflight required: call "
                        "`preflight_analytics_request(query, mode=\"chart\")` "
                        "before charting when semantic is enabled."
                    )

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

            return self._check_common_chart_preconditions_unlocked()

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
                "semantic_execution_attempted": self.semantic_execution_attempted,
                "semantic_fallback_recorded": self.semantic_fallback_recorded,
                "semantic_fallback_reason": self.semantic_fallback_reason,
                "semantic_preflight_cache_size": len(self.semantic_preflight_cache),
                "verified_query_surfaces": len(self.verified_query_surfaces),
                "analysis_path": self.analysis_path,
            }

    # ── Reset ───────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all tracked state. Called after successful generate_report."""
        with self.lock:
            self.search_models_count = 0
            self.explored_models.clear()
            self.explored_tables.clear()
            self.verified_query_surfaces.clear()
            self.execute_query_count = 0
            self.generate_chart_count = 0
            self.statistical_query_count = 0
            self.correlation_query_count = 0
            self.chart_types_generated.clear()
            self.semantic_tools_available = False
            self.semantic_tool_calls = 0
            self.semantic_preflight_ran = False
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
