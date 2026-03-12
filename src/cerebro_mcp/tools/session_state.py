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

    # Review gate
    review_approved: bool = False

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

    def record_generate_chart(self, chart_type: str, sql: str) -> None:
        with self.lock:
            self.generate_chart_count += 1

    def record_review_approval(self, role: str, notes: str = "") -> None:
        with self.lock:
            self.review_approved = True

    # ── Statistical helpers (advisory only) ─────────────────────────

    def is_statistical_query(self, sql: str) -> bool:
        """Check if SQL uses statistical functions. Soft signal only."""
        return bool(_STATISTICAL_RE.search(sql))

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
    ) -> tuple[bool, str]:
        """Gate for generate_report. Returns (passed, reason)."""
        if not settings.ENFORCE_CHART_PRECONDITIONS:
            return True, ""

        with self.lock:
            # Minimum chart count
            min_charts = settings.MIN_CHARTS_FOR_REPORT
            if len(chart_registry) < min_charts:
                return False, (
                    f"Insufficient charts: Generated {len(chart_registry)} "
                    f"chart(s), but the minimum required for a report is "
                    f"{min_charts}."
                )

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
                    )

            # Review approval gate
            if settings.REQUIRE_REVIEW_APPROVAL and not self.review_approved:
                return False, (
                    "Analysis not approved: A review agent must call "
                    "`approve_analysis` before generating the final report."
                )

        return True, ""

    def check_approval_preconditions(
        self,
    ) -> tuple[bool, str, list[str]]:
        """Gate for approve_analysis. Returns (can_approve, reason, warnings)."""
        if not settings.ENFORCE_CHART_PRECONDITIONS:
            return True, "", []

        warnings: list[str] = []

        with self.lock:
            # Hard gate: model exploration depth
            min_detailed = settings.MIN_MODELS_DETAILED
            if len(self.explored_models) < min_detailed:
                return False, (
                    f"Approval rejected: Explore at least {min_detailed} "
                    f"models via `get_model_details` to understand lineage "
                    f"and dimensions. "
                    f"(Currently explored: {len(self.explored_models)})."
                ), []

            # Hard gate: minimum exploratory queries
            min_queries = settings.MIN_EXPLORATORY_QUERIES
            if self.execute_query_count < min_queries:
                return False, (
                    f"Approval rejected: Run at least {min_queries} "
                    f"exploratory queries (EDA, distribution checks, "
                    f"dimensional queries) before approving. "
                    f"(Currently run: {self.execute_query_count})."
                ), []

            # Hard gate: statistical rigor
            min_stats = settings.MIN_STATISTICAL_QUERIES
            if self.statistical_query_count < min_stats:
                return False, (
                    f"Approval rejected: At least {min_stats} query must "
                    f"use statistical functions (quantiles, stddevPop, corr, "
                    f"median, percentile, etc.). Run EDA or distribution "
                    f"queries first. "
                    f"(Currently: {self.statistical_query_count} statistical "
                    f"queries)."
                ), []

            # Soft warning: charts generated with few queries
            if self.execute_query_count < 5 and self.generate_chart_count > 0:
                warnings.append(
                    "Only a few exploratory queries were run before "
                    "charting. Consider deeper EDA for more robust analysis."
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
            self.review_approved = False


# Global singleton
state = SessionState()
