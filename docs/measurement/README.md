# Measurement at Cerebro: MMM, MTA, and Unified Reconciliation

This subtree documents the measurement stack: Marketing Mix Modeling (MMM),
Multi-Touch Attribution (MTA), and the unified reconciliation that prevents
observational MTA credit from being mistaken for causal lift.

## Why this exists

Cerebro started with MMM only. MMM estimates *macro, ecosystem-level
incremental lift* under causal review. It is privacy-safe, robust to
identifier loss, and accounts for external factors. It is **not** good at
journey-level questions: "which app actions precede topup?", "what's the
typical path from offer-claim to swap?". Those questions need MTA.

But MTA alone is dangerous. It assigns *observational* credit across
observed touchpoints. The user who clicked an offer right before topping up
gets credit for the topup — even though that user might have topped up
anyway. Wikipedia's [Attribution (marketing)](https://en.wikipedia.org/wiki/Attribution_(marketing))
overview is blunt: attribution models can diverge from controlled lift
measurements because they rely on observational correlations.

The unified stack reconciles the two:

```
MMM estimates the incremental pie.
MTA divides the observed, trackable slice of that pie.
Experiments give the strongest causal validation.
```

## Decision tree — which workflow?

| Need | Workflow | Persona chain |
|---|---|---|
| Ecosystem-level "did this program work?" | MMM only | `mmm_analyst` → `mmm_causal_reviewer` → `mmm_simulator` (optional) |
| User-journey "which touchpoints precede X?" | MTA only | `mta_analyst` → `statistical_reviewer` |
| Attribute MMM-measured lift across journeys | Unified | `mmm_analyst` → `mmm_causal_reviewer` → `mta_analyst` → `unified_causal_reviewer` → `unified_allocator` |
| Clean A/B or geo holdout already running | Experiment | Wait for the readout — beats both MMM and MTA |

## Pages in this subtree

- [`mmm_overview.md`](mmm_overview.md) — MMM concepts: adstock, response curves, baseline, contribution decomposition.
- [`mta_overview.md`](mta_overview.md) — MTA concepts: journeys, lookback, coverage, attribution methods.
- [`unified_measurement.md`](unified_measurement.md) — how MMM and MTA combine, the calibration formula, and the gate.
- [`causal_review.md`](causal_review.md) — what `mmm_causal_reviewer` and `unified_causal_reviewer` enforce, and why.
- [`identity_grain.md`](identity_grain.md) — wallet vs app_user vs Safe vs owner vs session — how to choose, when each is wrong.
- [`glossary.md`](glossary.md) — adstock, lookback, coverage, leakage, removal effect, Shapley proxy, half-life, baseline, residual.
- [`examples/mta_app_topups.md`](examples/mta_app_topups.md) — end-to-end MTA worked example.
- [`examples/unified_pay_subsidy.md`](examples/unified_pay_subsidy.md) — end-to-end unified MMM + MTA reconciliation.

## How to read this subtree

Start with `unified_measurement.md` if you've used MMM before. Start with
`mmm_overview.md` if you haven't. Read `causal_review.md` before publishing
any report. Read `identity_grain.md` before any MTA run.

## References

- AI Digital, "Multi-Touch Attribution Explained" — pipeline view (collect → resolve → path → attribute → insight).
- CACI, "MTA vs MMM" — data-grain framing (aggregate vs user-level).
- Funnel.io, "Multi-touch attribution vs marketing mix modeling" — privacy and incrementality framing.
- Wikipedia, "Attribution (marketing)" — observational vs experimental divergence.
- Treasure Data, "MTA with Shapley Values" — timestamped journey + lookback + Shapley credit.
- Hakuhodo Marketing Mix Modeling Guidebook — adstock, response curves, causal-DAG checks.

## Persona contracts

The runtime contract for each persona lives in its `.md` file under
`src/cerebro_mcp/prompts/agents/`. The docs in this subtree are the
*concept* documentation; the persona files are the *operational* rules the
agent must follow. They cross-reference each other.
