# Unified MMM + MTA Measurement

This page describes how MMM and MTA combine into a single, gated workflow
that prevents observational MTA credit from being mistaken for causal lift.

## The core rule

```
MMM estimates the incremental pie.
MTA divides the observed, trackable slice of that pie.
Experiments give the strongest causal validation.
```

Restated as constraints:

1. **MTA cannot create lift beyond MMM.** Σ MTA-attributed conversions or value cannot exceed MMM-estimated incremental lift.
2. **MTA can only allocate within MMM.** It tells you *which observed touchpoints* are credited, not whether the lift was real.
3. **Untracked / offline / privacy-limited influence stays in the MMM baseline.** MTA cannot attribute what it cannot observe.

## When to use the unified workflow

Use the unified workflow when **all four** are true:

- You want to attribute lift to specific touchpoints.
- You also want a credible incrementality estimate.
- The data supports both a macro time-series MMM fit and a journey-grain MTA join.
- You can wait for the gates — this is not a one-shot query, it's a chain.

If only two or three of the four are true, run MMM-only or MTA-only and
state the limitation. Don't fake the unified workflow with one half missing.

## The chain

```
mmm_analyst
  → emits fitted (β, r, λ) per media + DAG
mmm_causal_reviewer
  → VERDICT: PASS | BLOCK
  (BLOCK halts the chain; apply prescribed fix and rerun)
mta_analyst
  → emits journey spine + coverage + attribution table
unified_causal_reviewer
  → VERDICT: PASS | BLOCK
  (BLOCK halts the chain; apply prescribed fix and rerun)
unified_allocator              [optional, only if recommendations requested]
  → emits bounded reallocation proposal
```

`generate_report` is **not** allowed until `unified_causal_reviewer` returns
PASS. The dispatcher's gating rule 3a enforces this.

## The calibration formula

After both reviewers pass, apply this calibration before reporting:

```
calibrated_credit_i =
  raw_mta_credit_i
  * mmm_incremental_lift
  / Σ raw_mta_credit_all_tracked_touchpoints
```

This rescales raw MTA shares so they sum to MMM-estimated incremental lift.
Note this is in MMM-KPI units (e.g. dollars of TVL, count of new DAU), not
raw conversion counts.

When tracked coverage is incomplete (almost always):

```
reported_credit_i        = calibrated_credit_i * tracked_coverage_rate
unexplained_or_untracked = mmm_incremental_lift - Σ reported_credit_i
```

The `unexplained_or_untracked` line **must** appear in the final report. It
represents the offline / privacy-limited / non-instrumented influence that
MTA cannot allocate. Omitting it overstates the explanatory power of the
touchpoint set.

## Worked example shape

A 6-month "did the Gnosis Pay subsidy program drive topups" question:

| Step | Persona | Output |
|---|---|---|
| 1 | `mmm_analyst` | Subsidy → topups: incremental lift = 12,400 topups [9,200, 15,800] (5th/95th) |
| 2 | `mmm_causal_reviewer` | PASS — DAG separates subsidy from concurrent retention campaign via dark period in Aug |
| 3 | `mta_analyst` | Of 14,800 observed topups, 9,400 have ≥1 touchpoint within 30d; coverage = 9,400 / 14,800 = 63.5% |
| 4 | `unified_causal_reviewer` | PASS — Σ raw MTA credit (14,800) > MMM lift (12,400) → calibration required and applied |
| 5 | `unified_allocator` | Calibrated allocation: subsidy_email 4,200, in_app_banner 3,100, push_notification 600; unexplained / untracked: 4,500 |

The walk-through with SQL is in
[`examples/unified_pay_subsidy.md`](examples/unified_pay_subsidy.md).

## What the unified review actually checks

Eight checks from `unified_causal_reviewer`:

1. MMM gate passed (cite `mmm_causal_reviewer` verdict).
2. Conversion consistency — MTA conversion maps to MMM KPI in scope and grain.
3. Incrementality bound — Σ MTA credit ≤ MMM lift midpoint (calibrate or block).
4. Coverage disclosure — tracked / total reported.
5. Leakage — `touch_ts <= conversion_ts` enforced; no negative lag in top paths.
6. Identity grain — stated and justified.
7. Selection bias — high-intent touchpoints flagged with caveat or experiment.
8. Method stability — rule-based vs Markov / Shapley divergence within tolerance.

If any check fails, the verdict is BLOCK. See
[`causal_review.md`](causal_review.md) for the full check list.

## Common pitfalls

- **Skipping MMM and going straight to MTA + allocation.** The recommendation has no incrementality basis — a tactic can dominate MTA credit while contributing zero lift.
- **Skipping the calibration when Σ MTA credit > MMM lift.** This is the most common form of double-counting.
- **Hiding the `unexplained_or_untracked` slice.** Visually, it can be 30–60% of the pie. Omitting it makes the touchpoint set look dispositive when it's only partial.
- **Using attribution language carelessly.** "Subsidy drove the topups" requires MMM PASS. "Subsidy preceded the topups in 35% of observed paths" is what MTA actually says.

## Cross-references

- [`mmm_overview.md`](mmm_overview.md) — MMM concepts.
- [`mta_overview.md`](mta_overview.md) — MTA concepts.
- [`causal_review.md`](causal_review.md) — what each reviewer enforces.
- [`identity_grain.md`](identity_grain.md) — choosing a user grain.
- [`glossary.md`](glossary.md) — terminology.
- [`examples/unified_pay_subsidy.md`](examples/unified_pay_subsidy.md) — full worked example.
