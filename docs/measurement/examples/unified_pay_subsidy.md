# Example: Unified MMM + MTA on Gnosis Pay Subsidy

End-to-end unified-measurement worked example. Uses synthetic numbers —
do not cite as real measurement. The goal is to show the full chain, the
calibration math, and how the gates compose.

## Question

"Did the Gnosis Pay subsidy program drive incremental topups in the last
6 months, and which touchpoints deserve credit for the trackable share?"

## Step 0 — Dispatch manifest

```
### Cerebro dispatch manifest
- Intent: unified_measurement
- Preflight route: hybrid_ready
- Parallelism: sequential
- Specialists to invoke (in order): [mmm_analyst, mmm_causal_reviewer, mta_analyst, unified_causal_reviewer, unified_allocator]
- Gates enforced: [mmm_causal_review: pending, unified_causal_review: pending, discovered_model_coverage: pending]
- Clarification asked: none
- Next action: call mmm_analyst
```

## Step 1 — `mmm_analyst`

Weekly spine over the last 24 months (104 weeks). Media columns:
`subsidy_outlay_usd`, `lm_rewards_outlay_usd`, `validator_apr_proxy`.
Control columns: gas-price proxy, ETH-USD return, holiday flag.

Multicollinearity check: |corr(subsidy, lm_rewards)| = 0.31 — fine.
|corr(subsidy, validator_apr)| = 0.08 — fine.

Baseline topups (median during bottom-decile-adstock weeks): **48 topups/week**.

Concave fit beats Hill on holdout MAE for the subsidy column → use
concave. Fitted parameters:

| Media | β | r | λ (decay) | bootstrap β [5th, 95th] |
|---|---:|---:|---:|---|
| subsidy_outlay_usd | 0.42 | 0.74 | 0.61 | [0.31, 0.55] |
| lm_rewards_outlay_usd | 0.18 | 0.69 | 0.55 | [0.11, 0.27] |
| validator_apr_proxy | 0.04 | 0.81 | 0.40 | [0.01, 0.09] |

Decomposition for the 6-month window: subsidy contributes **12,400 topups**
incremental [9,200, 15,800] (5th / 95th percentile of bootstrap).

## Step 2 — `mmm_causal_reviewer`

The DAG:

| From | To | Flag |
|---|---|---|
| subsidy_outlay | topup_count | direct |
| lm_rewards_outlay | topup_count | direct |
| validator_apr | topup_count | direct |
| gas_price | topup_count | control |
| holiday | topup_count | control |

Reviewer output:

| Check | Verdict | Evidence |
|---|---|---|
| Chronological | pass | weekly spend → weekly topups, no inverse-causation arrows |
| Non-inclusion | pass | none of the three media are inclusive of each other |
| Identifiability | pass | August 2025 dark period for subsidy preserves identifiability |

`VERDICT: PASS`. Chain proceeds to MTA.

## Step 3 — `mta_analyst`

Same model discovery and identity grain (`app_user`) as the MTA-only
example. Lookback = 30 days. Sample window = last 6 months (overlaps the
MMM window).

Coverage:

```
total_conversions             = 14,800
tracked_conversions           =  9,400
tracked_conversion_coverage   = 63.5%
```

Raw MTA credit (linear method, last 6 months):

| Touchpoint | Raw credit (conversion-units) |
|---|---:|
| view_topup_screen | 4,000 |
| swap | 2,800 |
| claim_offer | 1,800 |
| marketplace_payment | 1,400 |
| subsidy_email | 3,200 |
| in_app_banner | 1,200 |
| push_notification | 400 |
| **Σ raw credit** | **14,800** |

## Step 4 — `unified_causal_reviewer`

| Check | Verdict | Evidence | Required fix |
|---|---|---|---|
| 1. MMM gate passed | pass | mmm_causal_reviewer: PASS | — |
| 2. Conversion consistency | pass | both measure topups; KPI = topup_count | — |
| 3. Incrementality bound | conditional | Σ raw MTA = 14,800 > MMM lift = 12,400 → calibrate | apply calibration factor |
| 4. Coverage disclosure | pass | tracked = 63.5% reported in MTA artifact | — |
| 5. Leakage | pass | spine SQL enforces touch_ts ≤ conversion_ts; no negative lag | — |
| 6. Identity grain | pass | app_user, justified | — |
| 7. Selection bias | flagged | view_topup_screen dominates → caveat required | add caveat in report |
| 8. Method stability | pass | rank stable, share spread within tolerance for top-3 (excluding view_topup_screen) | — |

`VERDICT: PASS` with calibration applied.

### Calibration

```
calibrated_factor = MMM_lift / Σ_raw = 12,400 / 14,800 = 0.838
```

The calibration applied as ClickHouse SQL on the per-touchpoint credit table:

```sql
WITH calibrated AS (
  SELECT
    touchpoint_name,
    raw_mta_credit,
    raw_mta_credit / sum(raw_mta_credit) OVER () AS raw_mta_share,
    raw_mta_share * 12400.0 AS calibrated_lift
  FROM mta_touchpoint_credits
  WHERE window_start >= today() - INTERVAL 180 DAY
)
SELECT
  touchpoint_name,
  calibrated_lift,
  calibrated_lift * 0.635 AS reported_lift
FROM calibrated
ORDER BY reported_lift DESC;
```

| Touchpoint | Raw | Calibrated | × coverage 63.5% = reported |
|---|---:|---:|---:|
| view_topup_screen | 4,000 | 3,350 | 2,127 |
| swap | 2,800 | 2,346 | 1,490 |
| claim_offer | 1,800 | 1,508 | 958 |
| marketplace_payment | 1,400 | 1,173 | 745 |
| subsidy_email | 3,200 | 2,681 | 1,702 |
| in_app_banner | 1,200 | 1,005 | 638 |
| push_notification | 400 | 335 | 213 |
| **Σ reported** | | | **7,873** |

```
unexplained_or_untracked = 12,400 - 7,873 = 4,527 topups
```

## Step 5 — `unified_allocator`

Inputs cited:

- MMM incremental lift: 12,400 topups [9,200, 15,800]
- Calibrated MTA shares: per table above
- Tracked coverage: 63.5%
- unified_causal_reviewer: PASS (with selection-bias caveat on view_topup_screen)

Allocation table (simplified; current effort proxy in arbitrary cost units):

| Tactic | Current effort | Calibrated lift | ROI proxy | Proposed (raw) | Proposed (capped, ±30%) | Confidence |
|---|---:|---:|---:|---:|---:|---|
| subsidy_email | 100 | 1,702 | 17.0 | 130 | 130 | in_sample |
| in_app_banner | 80 | 638 | 8.0 | 95 | 95 | in_sample |
| push_notification | 40 | 213 | 5.3 | 30 | 30 | in_sample |

(Other touchpoints — swap, claim_offer, view_topup_screen — are user
actions, not direct levers, so they aren't allocated against.)

Recommended next experiment: dark-period the `subsidy_email` channel for
two weeks in a single geography. Expected information gain: tightens the
ROI estimate and provides a clean lift readout to validate the calibrated
share.

Caveats reproduced from the review:

- Observational attribution only.
- `view_topup_screen` is high-intent and over-credits the screen as a "cause".
- 4,527 topups (36.5% of MMM-estimated lift) are unexplained / untracked — likely offline or non-instrumented influence.

## Cross-references

- [`../unified_measurement.md`](../unified_measurement.md) — calibration formula and chain.
- [`../causal_review.md`](../causal_review.md) — what each gate enforces.
- [`mta_app_topups.md`](mta_app_topups.md) — MTA-only version of the topup example.
