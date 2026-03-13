"""Process-global, thread-safe session state tracker.

Tracks tool calls across the analysis workflow and enforces preconditions
on generate_chart and generate_report. All mutations are guarded by a
threading.Lock so the singleton is safe under concurrent tool calls.
"""

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


@dataclass
class SessionState:
    # Discovery tracking
    search_models_count: int = 0
    explored_models: set[str] = field(default_factory=set)
    explored_tables: set[str] = field(default_factory=set)

    # Execution tracking
    execute_query_count: int = 0
    generate_chart_count: int = 0
    statistical_query_count: int = 0
    correlation_query_count: int = 0
    chart_types_generated: set[str] = field(default_factory=set)

    # Thread safety
    lock: threading.Lock = field(default_factory=threading.Lock)

    # ── Record methods ──────────────────────────────────────────────

    def record_search_models(self, query: str, results_count: int) -> None:
        with self.lock:
            self.search_models_count += 1

    def record_get_model_details(self, model_name: str) -> None:
        with self.lock:
            self.explored_models.add(model_name)

    def record_describe_table(self, table: str) -> None:
        with self.lock:
            self.explored_tables.add(table)

    def record_execute_query(self, sql: str) -> None:
        with self.lock:
            self.execute_query_count += 1
            if self.is_statistical_query(sql):
                self.statistical_query_count += 1
            if self._is_correlation_query(sql):
                self.correlation_query_count += 1

    def record_generate_chart(
        self, chart_type: str, sql: str, series_field: str = "",
    ) -> None:
        with self.lock:
            self.generate_chart_count += 1
            self.chart_types_generated.add(chart_type)

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

    def check_chart_preconditions(self) -> tuple[bool, str]:
        """Gate for generate_chart. Returns (passed, reason)."""
        if not settings.ENFORCE_CHART_PRECONDITIONS:
            return True, ""

        with self.lock:
            if self.search_models_count == 0:
                return False, (
                    "Discovery skipped: You must call `search_models` first "
                    "to find relevant data models."
                )

            min_detailed = settings.MIN_MODELS_DETAILED
            if len(self.explored_models) < min_detailed:
                return False, (
                    f"Insufficient lineage exploration: You must call "
                    f"`get_model_details` on at least {min_detailed} models. "
                    f"(Currently explored: {len(self.explored_models)}). "
                    f"`describe_table` alone is not sufficient — it only "
                    f"shows column schema, not lineage or upstream "
                    f"dependencies."
                )

            min_verified = settings.MIN_TABLES_VERIFIED
            if len(self.explored_tables) < min_verified:
                return False, (
                    f"Insufficient schema verification: You must call "
                    f"`describe_table` on at least {min_verified} table(s) "
                    f"to verify exact column types before charting."
                )

        return True, ""

    def check_report_preconditions(
        self, chart_registry: dict
    ) -> tuple[bool, str, list[str]]:
        """Gate for generate_report. Returns (passed, reason, warnings)."""
        if not settings.ENFORCE_CHART_PRECONDITIONS:
            return True, "", []

        warnings: list[str] = []

        with self.lock:
            # Minimum chart count
            min_charts = settings.MIN_CHARTS_FOR_REPORT
            if len(chart_registry) < min_charts:
                return False, (
                    f"Insufficient charts: Generated {len(chart_registry)} "
                    f"chart(s), but the minimum required for a report is "
                    f"{min_charts}."
                ), []

            # Chart diversity: need at least one trend OR one breakdown
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

            # Minimum exploratory queries (hard gate)
            min_queries = settings.MIN_EXPLORATORY_QUERIES
            if self.execute_query_count < min_queries:
                return False, (
                    f"Insufficient exploration: Run at least {min_queries} "
                    f"exploratory queries (EDA, distribution checks, "
                    f"dimensional queries) before generating a report. "
                    f"(Currently run: {self.execute_query_count})."
                ), []

            # Statistical rigor (soft warning — was causing retry loops)
            min_stats = settings.MIN_STATISTICAL_QUERIES
            if self.statistical_query_count < min_stats:
                warnings.append(
                    f"No statistical queries detected (quantiles, stddev, "
                    f"corr, etc.). Consider running EDA with statistical "
                    f"functions for more robust analysis."
                )

            # Correlation analysis (soft warning — was causing retry loops)
            min_corr = settings.MIN_CORRELATION_QUERIES
            if (len(chart_registry) >= 3
                    and self.correlation_query_count < min_corr):
                warnings.append(
                    f"No correlation/regression queries detected. Consider "
                    f"using corr(), covarPop(), or simpleLinearRegression() "
                    f"to analyze relationships between metrics."
                )

            # Dimensional breakdown enforcement
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

            # Relational analysis enforcement
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

            # Soft warning: charts generated with few queries
            if self.execute_query_count < 5 and self.generate_chart_count > 0:
                warnings.append(
                    "Only a few exploratory queries were run before "
                    "charting. Consider deeper EDA for more robust analysis."
                )

            # Soft warning: no scatter chart for correlations
            if ("scatter" not in self.chart_types_generated
                    and self.generate_chart_count >= 3):
                warnings.append(
                    "No scatter chart generated. Consider adding a scatter "
                    "plot to visualize strong correlations (|r| > 0.5)."
                )

        return True, "", warnings

    # ── Reset ───────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all tracked state. Called after successful generate_report."""
        with self.lock:
            self.search_models_count = 0
            self.explored_models.clear()
            self.explored_tables.clear()
            self.execute_query_count = 0
            self.generate_chart_count = 0
            self.statistical_query_count = 0
            self.correlation_query_count = 0
            self.chart_types_generated.clear()


# Global singleton
state = SessionState()
