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


#: Severity decides what an unmet requirement DOES, not how bad it sounds.
#:
#: - `correctness` blocks. These mean the numbers are wrong — summing a stock
#:   measure over a date range, correlating a non-stationary series, hiding a
#:   residual bucket, double-counting aggregator volume. Publishing them
#:   publishes something false, so no disclosure substitutes for fixing them.
#: - `composition` does NOT block. These mean the report is thin, not wrong:
#:   too few charts, no dimensional split, no relational view, unused
#:   discoveries. The artifact ships with a "Known limitations" section naming
#:   each one, because a disclosed thin report beats no report at all — the
#:   failure this whole contract exists to stop was a session abandoning its
#:   work over a single missing breakdown chart.
#: - `advisory` never blocks and never disclaims; it is a nudge in the tool
#:   result. The statistical- and correlation-query counts have ALWAYS been
#:   advisory in code, while the instructions block, the SOP and the config
#:   comments all described them as hard rejects. This is where that stops.
SEVERITY_CORRECTNESS = "correctness"
SEVERITY_COMPOSITION = "composition"
SEVERITY_ADVISORY = "advisory"

#: Marks a warning as a composition shortfall that must be DISCLOSED in the
#: rendered artifact rather than merely logged. `check_report_preconditions`
#: returns warnings and every caller used to bind them to `_warnings` and throw
#: them away; the prefix lets `create_report_artifact` pick out the ones that
#: belong in the report's "Known limitations" section without changing the
#: function's return signature.
_LIMITATION_PREFIX = "LIMITATION: "


def split_limitations(warnings: list[str]) -> tuple[list[str], list[str]]:
    """Partition gate warnings into (disclosable limitations, plain advisories)."""
    limits = [
        w[len(_LIMITATION_PREFIX):] for w in warnings
        if w.startswith(_LIMITATION_PREFIX)
    ]
    rest = [w for w in warnings if not w.startswith(_LIMITATION_PREFIX)]
    return limits, rest


@dataclass(frozen=True)
class ReportRequirement:
    """One requirement, stated once and rendered everywhere.

    `id` is the stable key. Tests assert that the set of ids rendered by
    preflight, by the `generate_charts` tail and by the gate are identical —
    that assertion is what stops the four hand-written prose copies of this
    contract from drifting again.
    """

    id: str
    description: str
    how_to_fix: str
    severity: str = SEVERITY_COMPOSITION

    def as_line(self) -> str:
        return f"{self.description} — {self.how_to_fix}"


def report_requirements_for_tier(mode: str = "report") -> list[ReportRequirement]:
    """The full contract for a tier, derived from settings.

    Single source of truth for BOTH the advisory surfaces (`find`, preflight,
    the pre-execution chart scan, the `generate_charts` tail) and the enforcing
    gate. Mirrors the `_route` precedent in `tools/semantic/semantic.py`, which
    is documented as "Single source of truth so the two front doors can never
    drift" — the same reasoning applies with more force here, because this
    contract was previously restated by hand in four places
    (`server.py` twice, `charts.py`, `CLAUDE.md`) and every copy had drifted.

    Only the report tier has composition requirements; chart/answer tiers
    return the advisory entries alone, since `REPORT_REQUIRES_EXPLICIT_MODE`
    blocks them from producing a report artifact at all.
    """
    reqs: list[ReportRequirement] = []

    if mode == "report":
        reqs.append(ReportRequirement(
            id="min_charts",
            description=(
                f"At least {settings.MIN_CHARTS_FOR_REPORT} charts REFERENCED "
                f"in the report markdown"
            ),
            how_to_fix=(
                "the count is of charts you actually cite with "
                "`{{chart:ID}}`, not of charts generated — citing 2 of 6 "
                "counts as 2"
            ),
        ))
        if settings.REQUIRE_CHART_DIVERSITY:
            reqs.append(ReportRequirement(
                id="chart_diversity",
                description="A trend chart (line/area) or a breakdown (bar/pie)",
                how_to_fix="include at least one of either family",
            ))
        reqs.append(ReportRequirement(
            id="exploratory_queries",
            description=(
                f"At least {settings.MIN_EXPLORATORY_QUERIES} exploratory "
                f"queries"
            ),
            how_to_fix="run EDA / distribution checks with `execute_query`",
        ))
        if settings.REQUIRE_DIMENSIONAL_BREAKDOWN:
            reqs.append(ReportRequirement(
                id="dimensional_breakdown",
                description="At least one chart split by a dimension",
                how_to_fix=(
                    "set `series_field` on a chart, or use a "
                    "pie/treemap/heatmap/sankey type"
                ),
            ))
        if settings.REQUIRE_RELATIONAL_CHART:
            reqs.append(ReportRequirement(
                id="relational_analysis",
                description="At least one relational view",
                how_to_fix=(
                    "add a scatter/heatmap chart, OR run one correlation query "
                    "(`corr()`, `covarPop()`, `simpleLinearRegression()`)"
                ),
            ))
        if settings.ENFORCE_DISCOVERED_MODEL_COVERAGE:
            reqs.append(ReportRequirement(
                id="discovered_model_coverage",
                description=(
                    "Every model returned by `search_models` / "
                    "`discover_models` used or explicitly excluded"
                ),
                how_to_fix=(
                    "query it, call `get_model_details` on it, or sweep in one "
                    "call with `exclude_module` / `exclude_models_by_prefix` / "
                    "`exclude_all_discovered_except` / "
                    "`record_model_exclusion_batch` — do NOT loop the singular "
                    "`record_model_exclusion`"
                ),
            ))

    for key, rule_id, label in (
        ("ENFORCE_STOCK_FLOW_DISCIPLINE", "stock_flow_discipline",
         "No stock measure summed over a date range"),
        ("ENFORCE_RESIDUAL_BUCKET_DISCLOSURE", "residual_bucket_disclosure",
         "Residual buckets excluded by a filter are disclosed"),
        ("ENFORCE_STATIONARITY_ON_CORRELATIONS", "stationarity_on_correlations",
         "Time-series correlations address stationarity"),
        ("ENFORCE_AGGREGATOR_VOLUME_DEDUP", "aggregator_volume_dedup",
         "Aggregator volume is deduplicated"),
    ):
        if getattr(settings, key, False):
            reqs.append(ReportRequirement(
                id=rule_id,
                description=label,
                how_to_fix=(
                    "rewrite the SQL, or acknowledge the exception in the "
                    "chart's `title` / `description` / `override_reason`"
                ),
                severity=SEVERITY_CORRECTNESS,
            ))

    reqs.append(ReportRequirement(
        id="statistical_query",
        description=(
            f"{settings.MIN_STATISTICAL_QUERIES}+ statistical quer(ies) "
            f"(quantiles, stddev, corr)"
        ),
        how_to_fix="recommended, not required — this never blocks a report",
        severity=SEVERITY_ADVISORY,
    ))
    reqs.append(ReportRequirement(
        id="correlation_query",
        description=f"{settings.MIN_CORRELATION_QUERIES}+ correlation quer(ies)",
        how_to_fix="recommended, not required — this never blocks a report",
        severity=SEVERITY_ADVISORY,
    ))
    return reqs


def render_report_contract(mode: str = "report") -> str:
    """The contract as markdown, for any advisory surface.

    Deliberately states what blocks and what only discloses: a caller that
    believes every requirement is fatal behaves the same way as one that
    believes none are.
    """
    reqs = report_requirements_for_tier(mode)
    if not reqs:
        return ""
    blocking = [r for r in reqs if r.severity == SEVERITY_CORRECTNESS]
    shaping = [r for r in reqs if r.severity == SEVERITY_COMPOSITION]
    advisory = [r for r in reqs if r.severity == SEVERITY_ADVISORY]

    out: list[str] = []
    if shaping:
        out.append(
            "**Report composition** — unmet items do NOT block; the report "
            "ships with a \"Known limitations\" section naming them:"
        )
        out += [f"- {r.as_line()}" for r in shaping]
    if blocking:
        out.append(
            "\n**Correctness — these BLOCK** (they mean the numbers are wrong, "
            "not that the report is thin):"
        )
        out += [f"- {r.as_line()}" for r in blocking]
    if advisory:
        out.append("\n**Advisory only:**")
        out += [f"- {r.as_line()}" for r in advisory]
    return "\n".join(out)


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


def _format_report_gate_reason(gaps: list[str]) -> str:
    """Render all unmet report composition requirements as one message.

    The report tier's requirements are only discoverable at
    `generate_report` time, once every chart already exists. Returning them
    one per call meant a caller learned "add a series_field chart" after
    building seven without one, then learned the next requirement on the
    following round trip. A real session responded by abandoning the report
    and delivering markdown files instead.

    Single gap is returned verbatim so existing substring assertions and the
    original wording survive; multiple gaps are bundled with an explicit next
    action, because the failure arrives at the point where the caller is most
    likely to give up rather than iterate.
    """
    if len(gaps) == 1:
        return gaps[0]
    bullets = "\n".join(f"- {item}" for item in gaps)
    return (
        "Report quality gate: several requirements are unmet. Fix ALL of "
        "them in one pass — add the missing chart(s) with a single "
        "`generate_charts` batch call and run any missing quer(ies) — then "
        "retry `generate_report`. Do NOT abandon the report or fall back to "
        "writing markdown files.\n" + bullets
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
        # The routing result depends on the active AGENT ROLE (persona
        # boosts) and on the semantic-registry revision, not only on the
        # normalized question — a query+mode key served one role's cached
        # routing to another and survived registry reloads (R10 C6.4). The
        # cache itself lives per analysis cycle (see _SessionStateProxy),
        # which is what isolates OWNERS; these key parts fix staleness
        # WITHIN a cycle.
        from cerebro_mcp.runtime import runtime_state

        role = getattr(runtime_state, "current_agent_role", "") or ""
        try:
            from cerebro_mcp.loaders.semantic import semantic_registry

            registry_rev = str(semantic_registry.content_hash or "")[:12]
        except Exception:
            registry_rev = ""
        return (
            f"{query.strip().lower()}::{mode.strip().lower()}"
            f"::{role}::{registry_rev}"
        )

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
        database: str = "",
    ) -> None:
        with self.lock:
            self.explored_tables.add(table)
            # Curated raw databases (cow_db, governance_db, the RPC-scan
            # scratch DB) have no dbt models or semantic coverage, so
            # describe_table IS the authoritative discovery-and-lineage
            # surface for them.
            if database and database in settings.CURATED_RAW_DATABASES:
                self.explored_models.add(f"{database}.{table}")
                self.search_models_count = max(self.search_models_count, 1)
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

            # Two buckets, by severity — see ReportRequirement above.
            #
            # `gaps` BLOCKS: the numbers would be wrong. `limitations` does not
            # block; it is disclosed in the artifact instead.
            #
            # These all used to block, and used to be reported one at a time.
            # That ordering is the worst possible for the caller, because the
            # requirements are only discoverable at generate_report time, AFTER
            # every chart exists — a session learned "you need a series_field
            # chart" having already built seven flat time series, then would
            # have learned the next requirement on the following round trip.
            # Observed outcome: 28 queries, 7 charts, one complaint, and the
            # session abandoned the report and wrote markdown files.
            #
            # A thin report that says it is thin beats no report at all. A
            # wrong report does not, which is why the SQL-discipline rules
            # stay in `gaps`.
            gaps: list[str] = []
            limitations: list[str] = []

            min_charts = settings.MIN_CHARTS_FOR_REPORT
            if len(chart_registry) < min_charts:
                limitations.append(
                    f"Insufficient charts: Generated {len(chart_registry)} "
                    f"chart(s), but the minimum required for a report is "
                    f"{min_charts}."
                )

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
                    limitations.append(
                        "Chart diversity lacking: Report must include at "
                        "least one trend chart (line/area) or one breakdown "
                        "chart (bar/pie)."
                    )

            min_queries = settings.MIN_EXPLORATORY_QUERIES
            if self.execute_query_count < min_queries:
                limitations.append(
                    f"Insufficient exploration: Run at least {min_queries} "
                    f"exploratory queries (EDA, distribution checks, "
                    f"dimensional queries) before generating a report. "
                    f"(Currently run: {self.execute_query_count})."
                )

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
                    limitations.append(
                        "No dimensional breakdown: At least one chart must "
                        "use series_field to show data split by a dimension "
                        "(token, action type, segment, etc.), or use a "
                        "pie/treemap/heatmap/sankey chart type."
                    )

            if settings.REQUIRE_RELATIONAL_CHART:
                has_relational = any(
                    v.get("chart_type") in ("scatter", "heatmap")
                    for v in chart_registry.values()
                )
                has_correlation = self.correlation_query_count >= 1
                if not has_relational and not has_correlation:
                    limitations.append(
                        "No relational analysis: At least one scatter/"
                        "heatmap chart OR one correlation query (corr(), "
                        "covarPop(), simpleLinearRegression()) is required "
                        "for multi-dimensional analysis."
                    )

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
                    gaps.append(
                        "Quality discipline violation(s) on "
                        f"{len(violations)} chart(s). Either rewrite the SQL "
                        "to follow the rules in `_shared_quality_rules.md`, "
                        "or attach an explicit `override_reason` / "
                        "`description` to the chart that acknowledges the "
                        "antipattern with a stated reason.\n"
                        + "\n".join(detail_lines)
                    )

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
                    limitations.append(
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
                    )
                else:
                    observe_discovered_model_coverage("pass")
                    observe_quality_gate("discovered_model_coverage", "pass")

            # ONE verdict for the whole tier, and only correctness blocks.
            #
            # Everything is evaluated before anything is reported, so a caller
            # never fixes one requirement only to discover the next. Blocking
            # is reserved for the SQL-discipline rules, where proceeding would
            # publish wrong numbers; composition shortfalls travel out as
            # `limitations` and are disclosed IN the artifact by
            # `create_report_artifact`, which is what makes "the request always
            # produces something" true without making it produce something
            # false.
            if gaps:
                return False, _format_report_gate_reason(gaps), warnings

            if limitations:
                warnings.extend(_LIMITATION_PREFIX + item for item in limitations)

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


class _SessionStateProxy:
    """The stable state accessor (connector plan R10 §4.1, R9-audit P0-6).

    ``state`` was a process-global ``SessionState()`` shared by every
    caller, and it is imported BY VALUE all over the repo
    (``from ...session_state import state`` — research.py, charts.py,
    function-local imports). Rebinding the module attribute would therefore
    deglobalize nothing: those sites hold the old object forever. The audit
    prescription is a PROXY — every existing ``state.foo`` call site keeps
    working, including by-value imports (they hold the proxy), and
    resolution happens PER ACCESS:

    - connector profile active AND an analysis handle bound to the current
      request context -> that ``(owner, handle)`` cycle's ``SessionState``
      from ``runtime/analysis_registry.py``;
    - otherwise (stdio, internal_full, no handle) -> the legacy singleton,
      byte-for-byte the previous behavior.

    The proxy holds NO state of its own; ``__setattr__`` delegates too, so
    ``state.generate_chart_count = 0`` mutates the resolved cycle.
    """

    def _resolve(self) -> SessionState:
        from cerebro_mcp.tools.tool_policy import connector_profile_active

        if connector_profile_active():
            from cerebro_mcp.runtime.analysis_registry import (
                get_current_handle,
                state_for,
            )
            from cerebro_mcp.runtime.identity import get_current_owner

            handle = get_current_handle()
            if handle:
                resolved = state_for(get_current_owner(), handle)
                if resolved is not None:
                    return resolved
        return _default_state

    def __getattr__(self, name: str):
        return getattr(self._resolve(), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(self._resolve(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(self._resolve(), name)


# The legacy process-global cycle (stdio / internal_full / no handle).
_default_state = SessionState()

# The stable accessor every import site holds. See _SessionStateProxy.
state = _SessionStateProxy()
