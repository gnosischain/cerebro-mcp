# Glossary

Terminology used across the MMM / MTA / unified measurement stack.

## MMM terms

**adstock** — the carryover effect of a media variable. Spend in week N
still affects KPI in week N+k for some decay schedule. We use geometric
adstock with decay rate λ ∈ (0, 1).

**baseline KPI** — the level of the KPI when media spend is near zero.
Extracted as the median KPI during bottom-decile-adstock weeks. Required
before log-log fitting because `log(KPI - baseline)` must stay positive.

**β (beta)** — fitted coefficient on adstocked media. Larger β means
larger marginal contribution.

**concave fit** — `(KPI - baseline) = β × adstock^r` with `r < 1`. Fitted
via log-log: `simpleLinearRegression(log(adstock), log(KPI - baseline))`.

**Hill fit** — S-shaped saturation curve `KPI = 1 / (1 + (adstock / K)^(-S))`.
Fitted via grid search over (K, S) on mean-scaled inputs.

**λ (lambda)** — adstock decay rate. λ = 0.5 means half the prior week's
spend still contributes; λ = 0.1 means decay to ~10% in one week.

**marginal ROI** — `d(KPI) / d(spend)` at current spend. Use to decide
where the next dollar should go: shift from low marginal_roi to high.

**multicollinearity** — two media columns moving together (|corr| > 0.9).
Forces a merge / drop / segment because the model cannot separate their
effects.

**r** — curvature exponent in the concave fit. `r < 1` is diminishing
returns (good). `r > 1` is increasing returns (red flag — usually
collinearity).

**response curve** — the function from spend to KPI. Concave or Hill
depending on which fits better on holdout MAE.

**spine** — continuous weekly time grid with no missing weeks. Mandatory
before adstock so window functions have all preceding weeks.

## MTA terms

**attribution method** — the rule that assigns credit across touchpoints.
First-touch, last-touch, linear, time-decay, Markov removal-effect, sampled
Shapley proxy.

**conversion** — the user-level event we're attributing credit for. Topup,
swap, claim, payment, etc.

**coverage** — fraction of conversions / users with at least one
touchpoint inside the lookback window. Coverage <100% means the
touchpoint set cannot explain all observed conversions.

**half-life** — for time-decay attribution, the number of days at which
weight decays to 50%. Default 7 days.

**identity grain** — wallet, app_user, Safe, owner, session, or other. See
[`identity_grain.md`](identity_grain.md).

**journey** — ordered list of touchpoints preceding a conversion, within
the lookback window.

**leakage** — counting touchpoints that occurred *after* the conversion.
Inflates apparent attribution; reviewer rejects.

**lookback window** — number of days before a conversion within which
touchpoints count. Default 30 days; sweep 7 / 14 / 30 / 60 when volume
permits.

**Markov removal effect** — drop a touchpoint from the transition graph
and observe the change in CONVERT-state arrival probability. Higher
removal effect → more important touchpoint.

**path** — a journey with the order of touchpoints preserved.

**sampled Shapley proxy** — a ClickHouse-friendly approximation of Shapley
credit. Samples coalitions via hash-based filtering; uses observed
conversion rates as utility. Not a full game-theoretic Shapley
computation.

**selection bias** — high-intent touchpoints (e.g. "opened topup screen")
naturally precede conversion; observational attribution will over-credit
them.

**touchpoint** — an observed user-action that may have influenced a later
conversion.

## Unified terms

**calibration** — rescaling raw MTA shares so they sum to MMM-estimated
incremental lift. Formula: `calibrated_credit_i = raw_credit_i × MMM_lift /
Σ raw_credit`.

**coverage haircut** — multiplying calibrated credit by tracked coverage,
so reported credit reflects only the portion of MMM lift the touchpoints
can plausibly explain.

**incrementality bound** — the constraint Σ MTA credit ≤ MMM lift. The
unified reviewer's Check 3.

**unexplained / untracked** — the portion of MMM-estimated incremental
lift that no observed touchpoint can claim. Stays in the residual.
Disclosed explicitly in the final report.

## Cross-references

- [`mmm_overview.md`](mmm_overview.md), [`mta_overview.md`](mta_overview.md), [`unified_measurement.md`](unified_measurement.md), [`causal_review.md`](causal_review.md), [`identity_grain.md`](identity_grain.md).
