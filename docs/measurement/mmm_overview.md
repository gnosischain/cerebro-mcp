# MMM Overview

Marketing Mix Modeling (MMM) at Cerebro adapts the Hakuhodo / Google MMM
framework to on-chain incentives. It is the **macro, causal** half of the
measurement stack.

## Purpose

Estimate the incremental lift in a sector KPI (TVL, DEX volume, DAU, tx
count, bridge flows) attributable to each "media" variable (token
emissions, LM rewards, validator APR, bridge incentives), with credibility
intervals and a passing causal-DAG review.

## When to use

- "How much TVL did our LM rewards actually drive last quarter?"
- "If we cut emissions by 20%, what happens to volume?"
- "Which incentive program had the highest ROI?"
- Any question framed as macro / aggregate / ecosystem-level.

## When NOT to use

- "Which app actions precede a topup?" — that's MTA.
- Single-campaign A/B test with a clean control — use the experiment readout.
- <60 weekly rows of either KPI or media — MMM downgrades to "directional only".
- Sector with a known structural break (hardfork, exploit, tokenomics rework) inside the window without an explicit step-dummy.

## Inputs

- A weekly time spine for the chosen sector + KPI window (≥2 years preferred).
- Media variables (one column per media) with non-negative weekly values.
- Control variables: gas price, ETH/stable macro proxy, holiday flags, protocol-launch flags.

## Outputs

- Fitted (β, r, λ) per media — with curve choice (concave or Hill) data-driven by holdout MAE.
- Per-week contribution decomposition — stacked-area chart over time.
- Bootstrap credibility interval (5th / 95th percentile) on each β.
- A markdown DAG submitted to `mmm_causal_reviewer` and a PASS / BLOCK verdict.

## SOP

See [`src/cerebro_mcp/prompts/agents/mmm_analyst.md`](../../src/cerebro_mcp/prompts/agents/mmm_analyst.md)
for the binding 10-step SOP and ClickHouse toolkit. The high-level shape:

1. Discover models for KPI + media.
2. Verify columns and grain.
3. Spine-fill (continuous weekly spine, no missing weeks).
4. Multicollinearity check (|corr|>0.9 → merge / drop / segment).
5. Baseline extraction (median KPI during bottom-decile-adstock weeks).
6. Geometric adstock per media.
7. Concave + Hill grid-search fit; pick lower holdout MAE.
8. Contribution decomposition.
9. DAG handoff to `mmm_causal_reviewer`.
10. After PASS verdict: `generate_charts` + `generate_report`.

## Common failure modes

- **Sparse weekly data** — event-sourced rows can have missing weeks, silently breaking adstock windows. Use `WITH FILL STEP toIntervalWeek(1)` and coalesce to 0.
- **Co-launched programs** — two incentives that started the same week have correlated series. The reviewer blocks; fix is intervention / segmentation / front-door variable.
- **log(0) in concave fit** — happens if `KPI - baseline ≤ 0`. Fix: extract baseline (step 5) and regress `log(KPI - baseline)` on `log(adstock)`.
- **Single-coefficient ROI claims without bootstrap intervals** — never report ROI as a point estimate. Use the SQL bootstrap and report 5th/95th percentiles.

## Cross-references

- [`causal_review.md`](causal_review.md) — what `mmm_causal_reviewer` enforces.
- [`unified_measurement.md`](unified_measurement.md) — how MMM combines with MTA.
- [`glossary.md`](glossary.md) — adstock, baseline, response curve, Hill, β, r, λ.
