# Causal Review

Two reviewer personas gate the measurement stack. Both are hard blocks: a
report cannot ship without their PASS verdicts.

## `mmm_causal_reviewer`

Reviews the DAG submitted by `mmm_analyst` against three Hakuhodo Guidebook
checkpoints. See
[`src/cerebro_mcp/prompts/agents/mmm_causal_reviewer.md`](../../src/cerebro_mcp/prompts/agents/mmm_causal_reviewer.md).

| Check | What it enforces |
|---|---|
| 1. Chronological | Every edge X → Y has X moving before Y in the data window |
| 2. Non-inclusion | No two variables on the explanatory side are in an inclusion relation (e.g. total-DEX-volume AND Uniswap-volume) |
| 3. Identifiability | Each edge of interest is identifiable under single-door, back-door, or front-door criterion |

Verdict is `VERDICT: PASS | BLOCK`. On BLOCK, the reviewer prescribes one
of: intervention pattern, segmentation, front-door variable, or future
dark-period request.

## `unified_causal_reviewer`

Reviews the combined MMM + MTA artifact. See
[`src/cerebro_mcp/prompts/agents/unified_causal_reviewer.md`](../../src/cerebro_mcp/prompts/agents/unified_causal_reviewer.md).

| Check | What it enforces | What blocks |
|---|---|---|
| 1. MMM gate passed | `mmm_causal_reviewer` returned PASS | missing or BLOCK verdict |
| 2. Conversion consistency | MTA conversion maps to MMM KPI in scope and grain | KPI mismatch (e.g. weekly-TVL vs individual-topup) |
| 3. Incrementality bound | Σ MTA credit ≤ MMM lift midpoint | over-claim → calibrate or block |
| 4. Coverage disclosure | tracked / total conversions and users reported | missing coverage block |
| 5. Leakage | `touch_ts <= conversion_ts` enforced; no negative lag | post-conversion touches counted |
| 6. Identity grain | stated and justified | implicit / unjustified grain |
| 7. Selection bias | high-intent touchpoints have caveat or experiment | "viewed-screen" dominates with no test |
| 8. Method stability | rule-based vs Markov / Shapley rank within 2 / share within 25pp | wild divergence without confidence downgrade |

Verdict is `VERDICT: PASS | BLOCK`. On BLOCK the reviewer prescribes a
specific fix per failed row.

## Why two reviewers?

The MMM reviewer ensures the *macro* numbers are causally interpretable.
The unified reviewer ensures the *MTA breakdown* doesn't smuggle observational
correlation past the macro gate. Without the unified reviewer, the failure
mode is: MMM says "incremental lift = 1,000 conversions, well-identified",
MTA says "channel A gets 80% credit", and the report concludes "channel A
caused 800 conversions" — even though channel A's MTA credit reflects who
saw it, not who was caused to convert by it.

The eight unified checks exist to catch the specific patterns that allow
this slippage:

- **Check 3 (incrementality bound)** prevents Σ MTA > MMM double-counting baseline behavior as causal.
- **Check 5 (leakage)** prevents touches that occur after the conversion (returns to app, post-purchase pages) inflating attribution.
- **Check 7 (selection bias)** prevents "anyone who converted necessarily passed through screen X" being read as "screen X caused conversions".
- **Check 8 (method stability)** flags when methods disagree wildly — a sign that the data doesn't actually support a single attribution story.

## Required output format

Both reviewers return a single markdown table plus a one-line verdict:

```markdown
| Check | Verdict | Evidence | Required fix |
|---|---|---|---|
| ... | pass / fail | ... | ... |

VERDICT: PASS | BLOCK

(If BLOCK) Required fixes:
1. ...
2. ...
```

The session must paste this verdict block into the final report's
methodology section.

## Causal-language policy

Even with a PASS verdict, language must match what the evidence supports:

| Evidence level | Allowed language |
|---|---|
| MMM PASS only | "incremental lift", "associated with", "drove" (in MMM units) |
| MTA only | "preceded", "associated with", "observed credit", "share of journeys" |
| MMM + unified PASS | "drove" (with calibrated number), "explained" (within tracked coverage) |
| Experiment-backed | "caused", "lifted by X" (with confidence interval) |

`marketing_analyst` enforces this for external-audience communication.

## Cross-references

- [`unified_measurement.md`](unified_measurement.md) — the chain and calibration.
- [`mmm_overview.md`](mmm_overview.md) — MMM concepts.
- [`mta_overview.md`](mta_overview.md) — MTA concepts.
