# MTA Overview

Multi-Touch Attribution (MTA) at Cerebro is the **user-journey,
observational** half of the measurement stack. It describes how observed
touchpoints precede conversions and divides credit across them.

## Purpose

Build conversion journeys from observed user actions and assign fractional
**observational** credit to touchpoints. MTA does not estimate causal lift.
It describes correlations along observed paths.

## When to use

- "Which app actions tend to precede a Gnosis Pay topup?"
- "What's the typical path from offer-claim to swap?"
- "How do first-touch and last-touch attribution compare for our marketplace conversions?"
- "Where in the funnel are users dropping off?"

## When NOT to use

- "Did the LM rewards program *cause* TVL to grow?" — that's MMM.
- Conversion volume <30 in window — descriptive funnel only, no attribution credit.
- No usable user identifier on either side — downgrade to aggregate funnel diagnostics.
- A clean A/B holdout exists — use the experiment readout; experiment evidence beats observational MTA.

## Inputs

- A touchpoint model: rows of `(user_id, timestamp, touchpoint_name, optional_value)`.
- A conversion model: rows of `(user_id, timestamp, conversion_name, optional_value)`.
- An identity grain choice (see [`identity_grain.md`](identity_grain.md)).
- A lookback window (default 30 days; sweep 7/14/30/60 when volume permits).

## Outputs

- A journey spine: per-conversion ordered touchpoint lists with lag distributions.
- A coverage report: tracked conversions / total conversions, tracked users / total users.
- A funnel and path diagnostics block (`windowFunnel`, `sequenceMatch`, top-K paths).
- An attribution comparison table: first-touch / last-touch / linear / time-decay /
  Markov removal-effect / sampled-Shapley-proxy credit per touchpoint, side-by-side.
- Caveats: observational, coverage haircut, selection-bias warning.

## SOP

See [`src/cerebro_mcp/prompts/agents/mta_analyst.md`](../../src/cerebro_mcp/prompts/agents/mta_analyst.md)
for the binding rules and the ClickHouse toolkit. The high-level shape:

1. Discover touchpoint and conversion models with `search_models` / `discover_models`.
2. Verify columns with `describe_table`.
3. Build runtime mapping (user_id, timestamp, touchpoint name expression, value, identity grain).
4. Build journey spine (`touch_ts <= conversion_ts` AND within lookback).
5. Compute coverage.
6. Run funnel + path diagnostics.
7. Run rule-based attribution (first/last/linear/time-decay).
8. If volume ≥500 conversions: run Markov removal effect + sampled Shapley proxy.
9. Compare methods side-by-side; report where they agree / diverge.
10. Hand off to `unified_causal_reviewer` if part of a unified-measurement chain.

## Volume gates (hard)

| Conversions in window | Allowed |
|---:|---|
| <30 | Descriptive path / funnel only. No credit assignment. |
| 30–499 | Rule-based (first/last/linear/time-decay) + funnel. No Markov, no Shapley. |
| ≥500 | All methods including Markov removal effect and sampled Shapley proxy. |

## Common failure modes

- **Hardcoded model names.** The persona's "context examples" are illustrative; live runs must rediscover and `describe_table`. The SQL hygiene lint enforces this.
- **Post-conversion leakage.** Forgetting `touch_ts <= conversion_ts` makes downstream events look causal. Reviewer rejects.
- **Identity grain mismatch.** Wallet-grain attribution on a Safe contract over-credits behavioral touchpoints. Owner-grain on an EOA over-credits gas activity. State and justify the grain.
- **Selection bias.** A "viewed-topup-screen" touchpoint will dominate any attribution model — anyone who eventually topped up almost certainly viewed the screen first. Report a counterfactual or a caveat.
- **Method instability.** When first-touch and Markov disagree wildly, the result is directional at best. State this and downgrade confidence.

## Cross-references

- [`unified_measurement.md`](unified_measurement.md) — how MTA combines with MMM.
- [`causal_review.md`](causal_review.md) — what `unified_causal_reviewer` enforces.
- [`identity_grain.md`](identity_grain.md) — how to pick a user grain.
- [`glossary.md`](glossary.md) — touchpoint, lookback, coverage, removal effect, Shapley proxy.
