"""SQL templates for the Quarterly Review mini-app.

All templates use ``str.format`` with named placeholders bound to strings
from ``_build_sql_context`` in ``quarterly_review.py``. Filter inputs from
the user are never spliced into the SQL — only whitelisted dates.

### Schema notes (validated against the live warehouse on 2026-04-19)

Every ``api_*_daily`` table the warehouse exposes uses ``date`` (not
``day``) and, for numeric measures, a polymorphic ``value`` column (not
``tx_count``/``tvl_usd``/``staked_gno``). Consensus models use ``cnt``
(not ``count``) for counts. The ``api_execution_dau_daily`` and
``api_bridges_volume_daily`` tables that an earlier draft of this file
referenced **do not exist** — we substitute:

* ``api_execution_gnosis_app_users_daily`` for a daily "active users"
  proxy (``active_users`` column).
* ``api_bridges_token_netflow_daily_by_bridge`` for bridge volume
  (filtered to ``bridge != 'All'`` when doing breakdowns; ``bridge = 'All'``
  for totals).

### Query shape

KPI queries use an **outer SELECT over a UNION** so ``delta_pct`` can
reference the ``current`` / ``prior`` columns. Subquery results are cast
via ``toFloat64`` to sidestep ClickHouse's ``UInt64 vs Float64`` common-type
error when UNION-ing integer sums with float averages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FamilyId = Literal["execution", "tvl_volume", "bridges", "consensus"]


# =============================================================================
# Core family queries (headline KPI + trend + breakdown + scatter)
# =============================================================================

# ----- Execution ------------------------------------------------------------

KPI_EXECUTION = """
SELECT metric, current, prior,
       if(prior = 0 OR isNull(prior), NULL, (current - prior) / prior) AS delta_pct
FROM (
  SELECT 'tx_count' AS metric,
    toFloat64((SELECT sum(value) FROM dbt.api_execution_transactions_daily
               WHERE date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'))) AS current,
    toFloat64((SELECT sum(value) FROM dbt.api_execution_transactions_daily
               WHERE date BETWEEN toDate('{prior_start}') AND toDate('{prior_end}'))) AS prior
  UNION ALL
  SELECT 'active_users_avg',
    toFloat64((SELECT avg(active_users) FROM dbt.api_execution_gnosis_app_users_daily
               WHERE date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'))),
    toFloat64((SELECT avg(active_users) FROM dbt.api_execution_gnosis_app_users_daily
               WHERE date BETWEEN toDate('{prior_start}') AND toDate('{prior_end}')))
  UNION ALL
  SELECT 'new_users_sum',
    toFloat64((SELECT sum(new_users) FROM dbt.api_execution_gnosis_app_users_daily
               WHERE date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'))),
    toFloat64((SELECT sum(new_users) FROM dbt.api_execution_gnosis_app_users_daily
               WHERE date BETWEEN toDate('{prior_start}') AND toDate('{prior_end}')))
)
"""

TREND_EXECUTION = """
SELECT
  date AS day,
  if(date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'),
     '{quarter}', '{compare}') AS quarter,
  sum(value) AS tx_count
FROM dbt.api_execution_transactions_daily
WHERE date BETWEEN toDate('{prior_start}') AND toDate('{quarter_end}')
GROUP BY day, quarter
ORDER BY day
"""

BREAKDOWN_EXECUTION = """
SELECT
  toStartOfWeek(date) AS week,
  sum(value) AS tx_count
FROM dbt.api_execution_transactions_daily
WHERE date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}')
GROUP BY week
ORDER BY week
"""

SCATTER_EXECUTION = """
SELECT
  t.date AS day,
  toFloat64(t.value) AS tx_count,
  toFloat64(u.active_users) AS active_users,
  if(t.date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'),
     '{quarter}', '{compare}') AS quarter
FROM dbt.api_execution_transactions_daily t
INNER JOIN dbt.api_execution_gnosis_app_users_daily u ON u.date = t.date
WHERE t.date BETWEEN toDate('{prior_start}') AND toDate('{quarter_end}')
ORDER BY day
"""


# ----- TVL / Volume ---------------------------------------------------------

KPI_TVL_VOLUME = """
SELECT metric, current, prior,
       if(prior = 0 OR isNull(prior), NULL, (current - prior) / prior) AS delta_pct
FROM (
  SELECT 'tvl_avg' AS metric,
    toFloat64((SELECT avg(value) FROM dbt.api_execution_pools_tvl_daily
               WHERE date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'))) AS current,
    toFloat64((SELECT avg(value) FROM dbt.api_execution_pools_tvl_daily
               WHERE date BETWEEN toDate('{prior_start}') AND toDate('{prior_end}'))) AS prior
  UNION ALL
  SELECT 'volume_sum',
    toFloat64((SELECT sum(value) FROM dbt.api_execution_pools_volume_daily
               WHERE date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'))),
    toFloat64((SELECT sum(value) FROM dbt.api_execution_pools_volume_daily
               WHERE date BETWEEN toDate('{prior_start}') AND toDate('{prior_end}')))
  UNION ALL
  SELECT 'fees_sum',
    toFloat64((SELECT sum(value) FROM dbt.api_execution_pools_fees_usd_daily
               WHERE date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'))),
    toFloat64((SELECT sum(value) FROM dbt.api_execution_pools_fees_usd_daily
               WHERE date BETWEEN toDate('{prior_start}') AND toDate('{prior_end}')))
)
"""

TREND_TVL_VOLUME = """
SELECT
  date AS day,
  if(date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'),
     '{quarter}', '{compare}') AS quarter,
  sum(value) AS tvl
FROM dbt.api_execution_pools_tvl_daily
WHERE date BETWEEN toDate('{prior_start}') AND toDate('{quarter_end}')
GROUP BY day, quarter
ORDER BY day
"""

BREAKDOWN_TVL_VOLUME = """
SELECT
  toStartOfWeek(date) AS week,
  sum(value) AS volume
FROM dbt.api_execution_pools_volume_daily
WHERE date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}')
GROUP BY week
ORDER BY week
"""

SCATTER_TVL_VOLUME = """
SELECT
  t.date AS day,
  toFloat64(sum(t.value)) AS tvl,
  toFloat64(sum(v.value)) AS volume,
  if(t.date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'),
     '{quarter}', '{compare}') AS quarter
FROM dbt.api_execution_pools_tvl_daily t
INNER JOIN dbt.api_execution_pools_volume_daily v ON v.date = t.date
WHERE t.date BETWEEN toDate('{prior_start}') AND toDate('{quarter_end}')
GROUP BY day, quarter
ORDER BY day
"""


# ----- Bridges --------------------------------------------------------------
#
# ``bridge = 'All'`` is an aggregate row in the source model. Filter it out
# for breakdowns; keep it when summarising overall flow.

KPI_BRIDGES = """
SELECT metric, current, prior,
       if(prior = 0 OR isNull(prior), NULL, (current - prior) / prior) AS delta_pct
FROM (
  SELECT 'netflow_sum' AS metric,
    toFloat64((SELECT sum(value) FROM dbt.api_bridges_token_netflow_daily_by_bridge
               WHERE bridge = 'All' AND date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'))) AS current,
    toFloat64((SELECT sum(value) FROM dbt.api_bridges_token_netflow_daily_by_bridge
               WHERE bridge = 'All' AND date BETWEEN toDate('{prior_start}') AND toDate('{prior_end}'))) AS prior
  UNION ALL
  SELECT 'distinct_tokens',
    toFloat64((SELECT uniqExact(token) FROM dbt.api_bridges_token_netflow_daily_by_bridge
               WHERE bridge = 'All' AND date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'))),
    toFloat64((SELECT uniqExact(token) FROM dbt.api_bridges_token_netflow_daily_by_bridge
               WHERE bridge = 'All' AND date BETWEEN toDate('{prior_start}') AND toDate('{prior_end}')))
)
"""

TREND_BRIDGES = """
SELECT
  date AS day,
  if(date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'),
     '{quarter}', '{compare}') AS quarter,
  sum(value) AS netflow
FROM dbt.api_bridges_token_netflow_daily_by_bridge
WHERE bridge = 'All'
  AND date BETWEEN toDate('{prior_start}') AND toDate('{quarter_end}')
GROUP BY day, quarter
ORDER BY day
"""

BREAKDOWN_BRIDGES = """
SELECT
  bridge,
  sum(value) AS netflow
FROM dbt.api_bridges_token_netflow_daily_by_bridge
WHERE bridge != 'All'
  AND date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}')
GROUP BY bridge
ORDER BY abs(netflow) DESC
LIMIT 10
"""

SCATTER_BRIDGES = """
SELECT
  date AS day,
  toFloat64(sum(if(value > 0, value, 0))) AS inflow,
  toFloat64(sum(if(value < 0, -value, 0))) AS outflow,
  if(date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'),
     '{quarter}', '{compare}') AS quarter
FROM dbt.api_bridges_token_netflow_daily_by_bridge
WHERE bridge = 'All'
  AND date BETWEEN toDate('{prior_start}') AND toDate('{quarter_end}')
GROUP BY day, quarter
ORDER BY day
"""


# ----- Consensus ------------------------------------------------------------

KPI_CONSENSUS = """
SELECT metric, current, prior,
       if(prior = 0 OR isNull(prior), NULL, (current - prior) / prior) AS delta_pct
FROM (
  SELECT 'active_validators_avg' AS metric,
    toFloat64((SELECT avg(cnt) FROM dbt.api_consensus_validators_active_daily
               WHERE date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'))) AS current,
    toFloat64((SELECT avg(cnt) FROM dbt.api_consensus_validators_active_daily
               WHERE date BETWEEN toDate('{prior_start}') AND toDate('{prior_end}'))) AS prior
  UNION ALL
  SELECT 'staked_avg',
    toFloat64((SELECT avg(value) FROM dbt.api_consensus_staked_daily
               WHERE date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'))),
    toFloat64((SELECT avg(value) FROM dbt.api_consensus_staked_daily
               WHERE date BETWEEN toDate('{prior_start}') AND toDate('{prior_end}')))
)
"""

TREND_CONSENSUS = """
SELECT
  toDate(date) AS day,
  if(toDate(date) BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'),
     '{quarter}', '{compare}') AS quarter,
  avg(cnt) AS active_validators
FROM dbt.api_consensus_validators_active_daily
WHERE toDate(date) BETWEEN toDate('{prior_start}') AND toDate('{quarter_end}')
GROUP BY day, quarter
ORDER BY day
"""

BREAKDOWN_CONSENSUS = """
SELECT
  toStartOfWeek(toDate(date)) AS week,
  avg(cnt) AS active_validators
FROM dbt.api_consensus_validators_active_daily
WHERE toDate(date) BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}')
GROUP BY week
ORDER BY week
"""

SCATTER_CONSENSUS = """
SELECT
  toDate(v.date) AS day,
  toFloat64(v.cnt) AS active_validators,
  toFloat64(s.value) AS staked,
  if(toDate(v.date) BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'),
     '{quarter}', '{compare}') AS quarter
FROM dbt.api_consensus_validators_active_daily v
INNER JOIN dbt.api_consensus_staked_daily s ON toDate(s.date) = toDate(v.date)
WHERE toDate(v.date) BETWEEN toDate('{prior_start}') AND toDate('{quarter_end}')
ORDER BY day
"""


KPI_QUERIES: dict[FamilyId, str] = {
    "execution": KPI_EXECUTION,
    "tvl_volume": KPI_TVL_VOLUME,
    "bridges": KPI_BRIDGES,
    "consensus": KPI_CONSENSUS,
}

TREND_QUERIES: dict[FamilyId, str] = {
    "execution": TREND_EXECUTION,
    "tvl_volume": TREND_TVL_VOLUME,
    "bridges": TREND_BRIDGES,
    "consensus": TREND_CONSENSUS,
}

BREAKDOWN_QUERIES: dict[FamilyId, str] = {
    "execution": BREAKDOWN_EXECUTION,
    "tvl_volume": BREAKDOWN_TVL_VOLUME,
    "bridges": BREAKDOWN_BRIDGES,
    "consensus": BREAKDOWN_CONSENSUS,
}

SCATTER_QUERIES: dict[FamilyId, str] = {
    "execution": SCATTER_EXECUTION,
    "tvl_volume": SCATTER_TVL_VOLUME,
    "bridges": SCATTER_BRIDGES,
    "consensus": SCATTER_CONSENSUS,
}


# =============================================================================
# Tier-A analysis templates
# =============================================================================


@dataclass
class TemplateChartSpec:
    sql: str
    chart_type: str
    x_field: str
    y_field: str
    title: str
    series_field: str = ""
    change_field: str = ""
    max_rows: int = 2000
    database: str = "dbt"

    def render(self, ctx: dict[str, str]) -> dict:
        return {
            "sql": self.sql.format(**ctx),
            "database": self.database,
            "chart_type": self.chart_type,
            "x_field": self.x_field,
            "y_field": self.y_field,
            "series_field": self.series_field,
            "change_field": self.change_field,
            "title": self.title,
            "max_rows": self.max_rows,
        }


@dataclass
class Template:
    id: str
    name: str
    default_title: str
    default_conclusion_hint: str
    chart_specs: list[TemplateChartSpec] = field(default_factory=list)


# ---- Retention / churn (uses gnosis_app_users — the only daily user feed) ----

_USER_RETENTION_SQL = """
SELECT
  toStartOfMonth(date) AS month,
  sum(new_users)       AS new_users,
  avg(active_users)    AS active_users_avg,
  sum(returning_users) AS returning_users,
  sum(reactivated_users) AS reactivated_users
FROM dbt.api_execution_gnosis_app_users_daily
WHERE date BETWEEN toDate('{prior_start}') AND toDate('{quarter_end}')
GROUP BY month
ORDER BY month
"""

_USER_CHURN_SQL = """
SELECT bucket, value FROM (
  SELECT 'new' AS bucket,
    toFloat64((SELECT sum(new_users) FROM dbt.api_execution_gnosis_app_users_daily
               WHERE date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'))) AS value
  UNION ALL
  SELECT 'returning',
    toFloat64((SELECT sum(returning_users) FROM dbt.api_execution_gnosis_app_users_daily
               WHERE date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}')))
  UNION ALL
  SELECT 'reactivated',
    toFloat64((SELECT sum(reactivated_users) FROM dbt.api_execution_gnosis_app_users_daily
               WHERE date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}')))
)
"""


# ---- LTV proxy: total fees paid per top pool (there's no per-address feed) --

_POOL_FEES_SQL = """
SELECT
  coalesce(label, 'unlabeled') AS pool,
  sum(value) AS fees_usd
FROM dbt.api_execution_pools_fees_usd_daily
WHERE date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}')
GROUP BY pool
ORDER BY fees_usd DESC
LIMIT 25
"""


# ---- Feature adoption: per-pool volume Q-o-Q ----

_POOL_ADOPTION_SQL = """
SELECT
  coalesce(label, 'unlabeled') AS pool,
  sum(if(date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'), value, 0)) AS volume_current,
  sum(if(date BETWEEN toDate('{prior_start}')   AND toDate('{prior_end}'),   value, 0)) AS volume_prior,
  if(sum(if(date BETWEEN toDate('{prior_start}') AND toDate('{prior_end}'), value, 0)) = 0, NULL,
     (sum(if(date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}'), value, 0)) -
      sum(if(date BETWEEN toDate('{prior_start}')   AND toDate('{prior_end}'),   value, 0))) /
     sum(if(date BETWEEN toDate('{prior_start}') AND toDate('{prior_end}'), value, 0))
  ) AS delta_pct
FROM dbt.api_execution_pools_volume_daily
WHERE date BETWEEN toDate('{prior_start}') AND toDate('{quarter_end}')
GROUP BY pool
HAVING volume_current > 0
ORDER BY volume_current DESC
LIMIT 20
"""


# ---- Bridge segmentation: netflow by bridge (scatter-style) ----

_BRIDGE_SEGMENTATION_SQL = """
SELECT
  bridge,
  sum(if(value > 0, value, 0)) AS inflow,
  sum(if(value < 0, -value, 0)) AS outflow,
  sum(value) AS netflow
FROM dbt.api_bridges_token_netflow_daily_by_bridge
WHERE bridge != 'All'
  AND date BETWEEN toDate('{quarter_start}') AND toDate('{quarter_end}')
GROUP BY bridge
ORDER BY abs(netflow) DESC
LIMIT 20
"""


TEMPLATES: dict[str, Template] = {
    "cohort_retention": Template(
        id="cohort_retention",
        name="User retention trend",
        default_title="Monthly user retention",
        default_conclusion_hint="Describe how active / returning / reactivated users evolved through the quarter.",
        chart_specs=[
            TemplateChartSpec(
                sql=_USER_RETENTION_SQL,
                chart_type="line",
                x_field="month",
                y_field="active_users_avg",
                series_field="",
                title="Monthly user retention (Gnosis App)",
            ),
        ],
    ),
    "address_ltv": Template(
        id="address_ltv",
        name="Top pools by fees (LTV proxy)",
        default_title="Top pools by quarterly fees",
        default_conclusion_hint="Top-25 pools by fees paid this quarter — a protocol-level LTV proxy.",
        chart_specs=[
            TemplateChartSpec(
                sql=_POOL_FEES_SQL,
                chart_type="bar",
                x_field="pool",
                y_field="fees_usd",
                title="Top 25 pools by fee revenue",
            ),
        ],
    ),
    "churn": Template(
        id="churn",
        name="New vs returning vs reactivated",
        default_title="User composition this quarter",
        default_conclusion_hint="Split of new vs returning vs reactivated users — a churn-direction proxy.",
        chart_specs=[
            TemplateChartSpec(
                sql=_USER_CHURN_SQL,
                chart_type="pie",
                x_field="bucket",
                y_field="value",
                title="User composition (Gnosis App)",
            ),
        ],
    ),
    "feature_adoption": Template(
        id="feature_adoption",
        name="Feature adoption Q-o-Q",
        default_title="Pool volume Q-o-Q",
        default_conclusion_hint="Top pools with quarter-over-quarter volume delta — which features grew.",
        chart_specs=[
            TemplateChartSpec(
                sql=_POOL_ADOPTION_SQL,
                chart_type="bar",
                x_field="pool",
                y_field="volume_current",
                change_field="delta_pct",
                title="Top pools this quarter with Q-o-Q delta",
            ),
        ],
    ),
    "segmentation": Template(
        id="segmentation",
        name="Bridge segmentation",
        default_title="Inflow vs outflow by bridge",
        default_conclusion_hint="Which bridges are net-accumulators vs drainers this quarter.",
        chart_specs=[
            TemplateChartSpec(
                sql=_BRIDGE_SEGMENTATION_SQL,
                chart_type="scatter",
                x_field="inflow",
                y_field="outflow",
                title="Bridge inflow vs outflow (size = |netflow|)",
            ),
        ],
    ),
}
