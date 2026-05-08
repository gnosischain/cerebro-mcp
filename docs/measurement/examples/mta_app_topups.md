# Example: MTA on Gnosis Pay Topups

End-to-end MTA worked example. Uses synthetic numbers — do not cite as
real measurement. The goal is to show the shape of a clean MTA run, the
SQL that supports each step, and the format the persona must produce.

## Question

"Which app actions tend to precede a Gnosis Pay topup, and how do
attribution methods compare for the last 60 days?"

## Step 0 — Dispatch manifest

```
### Cerebro dispatch manifest
- Intent: mta
- Preflight route: hybrid_ready
- Parallelism: sequential
- Specialists to invoke (in order): [mta_analyst, statistical_reviewer]
- Gates enforced: [discovered_model_coverage: pending]
- Clarification asked: none
- Next action: call mta_analyst
```

## Step 1 — Discovery

```text
search_models("topup gpay gnosis pay user event activity")
search_models("touchpoint conversion app event")
```

Discovered models (illustrative — runtime discovery would replace this):

| Model | Classification | Used? | Reason |
|---|---|---|---|
| `int_execution_gnosis_app_user_events` | touchpoint | yes | covers swap/claim/marketplace events with timestamps |
| `int_execution_gnosis_app_gpay_topups` | conversion | yes | conversion model |
| `int_execution_gnosis_app_users_current` | identity | yes | maps app_user_id → wallet (bridge) |
| `fct_execution_gnosis_app_retention_monthly` | aggregate | excluded | grain is monthly aggregate, not journey |
| `fct_execution_gnosis_app_churn_monthly` | aggregate | excluded | same reason |

`describe_table` then verifies columns:

- touchpoint: `app_user_id`, `event_ts`, `event_type`, NULL value column
- conversion: `app_user_id`, `topup_ts`, `topup_amount_usd`

## Step 2 — Identity grain

**Grain: `app_user`.** The conversion is an app-side event keyed by
`app_user_id`. Touchpoints share the same key. No bridge needed.

Tradeoff: wallet-only users without an `app_user_id` are excluded — that's
explicit in the coverage block.

## Step 3 — Runtime mapping

```json
{
  "touchpoint_model": "int_execution_gnosis_app_user_events",
  "conversion_model": "int_execution_gnosis_app_gpay_topups",
  "user_id_col": "app_user_id",
  "conversion_user_id_col": "app_user_id",
  "touchpoint_ts_col": "event_ts",
  "conversion_ts_col": "topup_ts",
  "touchpoint_name_expr": "event_type",
  "conversion_name_expr": "'topup'",
  "touchpoint_value_expr": "NULL",
  "conversion_value_expr": "topup_amount_usd",
  "identity_grain": "app_user"
}
```

## Step 4 — Journey spine

```sql
WITH touchpoints AS (
  SELECT
    lower(app_user_id) AS user_id,
    toDateTime(event_ts) AS touch_ts,
    event_type AS touchpoint_name,
    'int_execution_gnosis_app_user_events' AS source_model
  FROM dbt.int_execution_gnosis_app_user_events
  WHERE event_ts >= today() - INTERVAL 90 DAY
),
conversions AS (
  SELECT
    lower(app_user_id) AS user_id,
    toDateTime(topup_ts) AS conversion_ts,
    'topup' AS conversion_name,
    topup_amount_usd AS conversion_value
  FROM dbt.int_execution_gnosis_app_gpay_topups
  WHERE topup_ts >= today() - INTERVAL 60 DAY
),
journeys AS (
  SELECT
    c.user_id, c.conversion_ts, c.conversion_name, c.conversion_value,
    t.touch_ts, t.touchpoint_name, t.source_model,
    dateDiff('day', t.touch_ts, c.conversion_ts) AS lag_days
  FROM conversions c
  INNER JOIN touchpoints t
    ON t.user_id = c.user_id
   AND t.touch_ts <= c.conversion_ts
   AND t.touch_ts >= c.conversion_ts - INTERVAL 30 DAY
)
SELECT * FROM journeys ORDER BY user_id, conversion_ts, touch_ts LIMIT 1000;
```

## Step 5 — Coverage

```
total_conversions          = 14,800
tracked_conversions        =  9,400
tracked_conversion_coverage = 63.5%
total_converting_users     =  6,200
tracked_users              =  4,100
tracked_user_coverage      = 66.1%
```

36.5% of topups have **no** observed touchpoint within 30 days. Either the
user is purely off-app or events were not captured. Disclose, don't
attribute.

## Step 6 — Funnel + path diagnostics

Top-5 paths by frequency (last 60 days, lookback 30):

| Path | Users | Median lag (days) |
|---|---:|---:|
| view_topup_screen → topup | 1,820 | 0 |
| swap → view_topup_screen → topup | 740 | 1 |
| claim_offer → swap → topup | 410 | 3 |
| marketplace_payment → topup | 280 | 7 |
| (no touchpoints) → topup | 5,400 | — |

`view_topup_screen` is a high-intent touchpoint — a selection-bias flag.

## Step 7 — Attribution comparison

| Touchpoint | First touch | Last touch | Linear | Time decay (HL=7d) | Markov | Shapley proxy |
|---|---:|---:|---:|---:|---:|---:|
| view_topup_screen | 12% | 52% | 28% | 38% | 0.41 | 0.36 |
| swap | 28% | 18% | 24% | 22% | 0.18 | 0.21 |
| claim_offer | 22% | 8% | 16% | 14% | 0.12 | 0.14 |
| marketplace_payment | 18% | 12% | 14% | 11% | 0.10 | 0.11 |
| other | 20% | 10% | 18% | 15% | 0.19 | 0.18 |

## Step 8 — Interpretation

- `view_topup_screen` dominates last-touch and time-decay. This is selection bias — anyone who topped up almost certainly viewed the topup screen first. Treat as a checkpoint, not a cause.
- `swap` is consistently 18–28% across methods → stable.
- `claim_offer` is consistently 8–16% → stable, smaller share.
- Method stability check: top-3 rank order is the same across methods. Top-touchpoint share spread is 12% → 52% (40pp). This exceeds the unified-reviewer 25pp tolerance. **Confidence is downgraded to directional.**

## Step 9 — Caveats

- Observational attribution only. No causal claim is made.
- 36.5% of topups have no observed touchpoint within 30 days.
- `view_topup_screen` is high-intent and likely over-credited. Do not act on its share without an experiment.
- Method stability is below tolerance — directional confidence only.

## Step 10 — Statistical reviewer handoff

The session passes the attribution comparison + sample sizes to
`statistical_reviewer` for sample-size and methodology review before the
final report renders.

## Cross-references

- [`../mta_overview.md`](../mta_overview.md)
- [`../identity_grain.md`](../identity_grain.md)
- [`../glossary.md`](../glossary.md)
