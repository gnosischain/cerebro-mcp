# Shared Quality Rules — applies to every analysis persona

> **Operational status:** every cerebro analysis persona that emits findings, charts, or reports must apply the rules below. The report enforcement gates in `tools/session_state.py` enforce a subset of these rules at `generate_*_report` time; the rest are agent responsibilities. A finding that violates a rule below must be corrected, removed, or accompanied by an explicit override note in the surrounding narrative before the report is generated.

---

## 0. SQL dialect — ClickHouse, always

The cerebro warehouse is **ClickHouse**. Every SQL fragment you write — in `execute_query`, `start_query`, chart specs, and report-embedded queries — must be valid ClickHouse. PostgreSQL / Snowflake / BigQuery idioms will silently fail or, worse, return wrong results.

Use these ClickHouse-native patterns:

| Need | ClickHouse | Do NOT use |
|---|---|---|
| Value at latest timestamp | `argMax(value, ts)` | `DISTINCT ON`, window-function workarounds |
| Quantiles / percentiles | `quantile(0.5)(x)`, `quantilesExact(0.25, 0.5, 0.75)(x)` | `PERCENTILE_CONT`, `MEDIAN()` |
| Pearson correlation | `corr(x, y)` | manually computed |
| Linear regression | `simpleLinearRegression(x, y)` | hand-rolled OLS |
| Spearman correlation | `corr(rank(x) OVER (), rank(y) OVER ())` (no native function) | (do not assume one exists) |
| Bootstrap iteration | `arrayJoin(range(N))` joined to the source CTE | recursive CTEs |
| Date truncation | `toStartOfMonth`, `toStartOfWeek`, `toStartOfDay`, `toUnixTimestamp` | `DATE_TRUNC` |
| Distinct counts | `uniqExact`, `uniqHLL12`, `groupBitmap` | `COUNT(DISTINCT ...)` (allowed but slow) |
| Conditional aggregation | `countIf(cond)`, `sumIf(x, cond)` | `SUM(CASE WHEN ... END)` (allowed but verbose) |
| Joins | always state `INNER JOIN`, `LEFT JOIN`, etc. and use explicit `ON` or `USING` | bare `JOIN` (ClickHouse defaults can surprise) |

If you need a feature ClickHouse does not have natively (e.g., ADF stationarity test), state that and either approximate it in SQL (e.g., correlate first-differenced series) or hand the computation off to a downstream Python step.

---

## 1. Denominator discipline

Every percentage, share, or ranking must be computed against an **explicit, complete universe**. The most common failure mode is filtering out a residual / "Unknown" / unlabelled bucket and then reporting the remaining percentages as if they describe the whole.

- If a model has a residual bucket (e.g., `label = 'Unknown'`, `sector = ''`, `category IS NULL`), that bucket **must appear in the chart** unless you state explicitly in the chart subtitle and surrounding narrative that the chart shows "share of labelled X" and you report the labelled fraction of the universe.
- "Top-N" rankings on a filtered subset must say "top-N within {filter}" — never bare "top-N."
- Pie / treemap / sankey charts must include the residual bucket or carry a subtitle naming the excluded fraction.

> Negative example: `WHERE label != ''` followed by a sector pie chart presented as "Gnosis Chain transaction breakdown." This describes only the labelled minority and misleads any reader who does not read the SQL.
>
> Positive example: `GROUP BY label` with no exclusion filter, residual `''` rendered as "Unclassified" in the chart, with a subtitle "Unclassified = X% of total transactions; investigation deferred to the next cycle."

---

## 2. Stock vs flow

**Stock measures** describe a state at a point in time: TVL, balances, supply, snapshot wallet count, debt outstanding, queue depth. They are **never aggregated across time** — summing them is meaningless.

**Flow measures** describe activity over an interval: transaction count, trading volume, fee revenue, new addresses, gas used. They **may** be summed across time windows.

- For TVL: use `argMax(tvl_usd, date)` per pool per day, then sum across pools at a single date. Or constrain to `WHERE date = (SELECT max(date) FROM ...)`. Or use the canonical snapshot model `fct_execution_pools_snapshots`.
- For balances / supply / debt outstanding: same pattern — point-in-time only.
- If you write `SUM(tvl_usd)` over a date range, you have produced a meaningless number.

> Negative example: `SELECT SUM(tvl_usd) AS total_tvl FROM fct_execution_pools_daily WHERE date >= '2025-01-01'`. This is total-TVL-day units; not interpretable as money.
>
> Positive example: `SELECT date, sum(argMax(tvl_usd, date)) AS tvl_usd FROM fct_execution_pools_daily GROUP BY date ORDER BY date`. This is daily snapshot TVL.

---

## 3. Survivorship disclosure

Any analysis that filters to "currently active," "non-zero balance," "still onboarded," or any other survival condition must either (a) report the mortality / churn rate alongside, or (b) state explicitly in the methodology that the report is conditioned on survival.

Examples of implicit survivorship filters that must be disclosed:
- Pool universe limited to pools with TVL above a threshold ("we only see surviving pools").
- User cohort limited to wallets active in the last 30 days ("we only see retained users").
- Aggregator universe limited to those with non-zero volume ("we only see live aggregators").

---

## 4. Discovered-model-must-be-used

Every model returned by `search_models`, `discover_models`, or `discover_metrics` that is judged relevant to a headline finding must appear in at least one `execute_query` or `start_query` call **before** any `generate_*_report` call. If a discovered model is excluded, the report's methodology section must state the exclusion and the reason in one line.

This rule exists because the most decision-relevant model in any given report is repeatedly the model the agent discovered and then forgot to query. The pattern is structural and recurs across topics. A discovery without a query (or an explicit exclusion note) is a bug.

---

## 5. Stock-of-evidence matches strength of claim

Causal language is forbidden unless a causal identification strategy is named in the methodology section.

| Forbidden | Permitted |
|---|---|
| "X drove Y" | "X is associated with Y" |
| "X caused Y" | "X co-moves with Y" |
| "X explains Y" | "X precedes Y in the observed window" |
| "growth driven by X" | "growth coincides with X" |

Use causal language only when the methodology cites one of: difference-in-differences, instrumental variable, regression discontinuity, event study, randomised experiment, or a structural model with stated identifying assumptions.

---

## 6. Time-series correlations require stationarity context

Pearson `corr(x, y)` over two time-series columns is **almost always spurious** if either series is non-stationary (which most blockchain time series are: TVL, prices, cumulative anything). Two independently rising series will yield `r > 0.9` with no economic meaning.

Required treatment for any correlation between time series:
- State the series and their date column.
- Either (a) report ADF stationarity test on both series and re-run on first-differenced series if either is non-stationary, or (b) use Spearman rank correlation as a non-parametric alternative and acknowledge the limitation, or (c) compute correlation on the *change* in each series, not the level.
- For small panels (n < 200), include a bootstrap confidence interval. Headline-gate any claim whose 95% CI crosses zero.

ClickHouse implementation patterns:
```sql
-- First-differenced correlation
WITH d AS (
  SELECT date, x, y,
         x - lagInFrame(x) OVER (ORDER BY date) AS dx,
         y - lagInFrame(y) OVER (ORDER BY date) AS dy
  FROM source
)
SELECT corr(dx, dy) AS diff_corr FROM d WHERE dx IS NOT NULL AND dy IS NOT NULL;

-- Spearman approximation
SELECT corr(rank_x, rank_y) AS spearman
FROM (
  SELECT rank() OVER (ORDER BY x) AS rank_x,
         rank() OVER (ORDER BY y) AS rank_y
  FROM source
);

-- Bootstrap 95% CI on Pearson r (n_iter resamples)
WITH samples AS (
  SELECT i, arrayMap(_ -> (x, y), range(n)) AS pairs
  FROM (SELECT i FROM (SELECT arrayJoin(range(1000)) AS i)) outer
  CROSS JOIN (SELECT n, groupArray((x, y)) FROM source GROUP BY 1) inner
)
-- ... (continue with sample-level correlation; keep the iteration count small in production)
```

---

## 7. Revenue not GMV for monetised products

Any report touching a monetised product (payment cards, exchanges, lending markets, validator services, etc.) must distinguish:

- **Notional / GMV / volume**: total value flowing through the product.
- **Revenue**: fees, interest, interchange, or other income retained by the protocol.
- **Subsidy / cashback / incentive cost**: outflows that fund acquisition or activity.
- **Net contribution**: revenue − subsidy.

Surface revenue and subsidy on the same chart so net contribution is visible. Bare "ARPU" is forbidden; label as "fee revenue per user" or "payment volume per user." A pitch deck that surfaces only volume is misleading; a pitch deck that surfaces revenue without subsidy is incomplete.

---

## 8. Bare metric name forbidden

Labels like "ARPU," "TVL," "active users," "growth rate" without a qualifier are forbidden. Always specify:

- "ARPU" → "fee revenue per MAU" or "payment volume per MAU"
- "TVL" → "snapshot TVL on {date}" or "average daily TVL over {window}"
- "active users" → "wallet-distinct MAU" or "KYC-distinct MAU"
- "growth rate" → "MoM payment-volume growth" or "WoW new-address growth"

A bare metric name is a bug because two readers will interpret it differently and the report cannot adjudicate.

---

## 9. Never index a versioned payload by fixed position

Any array that is positional against an external schema — Snapshot's `vp_by_strategy` against a
proposal's `strategies`, `scores` against `choices`, an ABI-decoded tuple against its ABI — must be
addressed by **name**, resolved from the schema that same row was produced under. A hardcoded index,
or a guard on a hardcoded `length(...)`, silently breaks the day the schema changes and takes the
historical rows with it.

The canonical failure in this repo: `delegation_power` read `vp_by_strategy[4]`/`[5]` guarded on
`length(vps) = 5`. `gnosis.eth` had rewritten its strategy list three times, so **every** delegate
whose latest vote predated the newest layout reported 0 — 26% of all delegated voting power, silently.
The near-miss fix was worse: the delegation strategies appear in the opposite chain order in the
previous layout, so "take the last two entries" would have swapped mainnet and Gnosis Chain across
44,635 votes without changing a single total.

Three rules follow:

- **Resolve by name, from the row's own schema.** Join to the record that defines the layout and match
  on the name/identifier, not the offset. Match names as substrings when the family has variants
  (`delegation` also appears as `erc20-balance-of-delegation`).
- **A length guard is not a schema check.** `if(length(x) = N, ..., 0)` reads as defensive and behaves
  as a silent filter. If the shape is unexpected the answer is NULL, not a default.
- **Assert the eras, not just the current one.** A test that only exercises today's payload shape
  cannot see this class of bug. Pin at least one row from each historical layout.

---

## Operational note for agents

When you adopt any cerebro analysis persona, your first chart, first query, and first narrative paragraph should reflect these rules. If you write something that violates a rule, fix it before you call `generate_*_report`. The gates in `session_state.py` will catch many of these violations and reject the report; the gates that exist as soft warnings should be treated as hard bugs unless you have an explicit override reason in the report narrative.
