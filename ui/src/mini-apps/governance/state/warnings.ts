// Warning codes the server attaches to view state → what the warning strip shows.
//
// Every code the server can emit MUST have copy here: the strip passes unknown
// strings through unchanged (a free-text error is already a sentence), so a code
// without copy reached the page raw — "treasury_history_partial" sat in a yellow
// chip above the treasury until 2026-09-26. tests/test_governance_explorer.py
// checks this map against the codes governance_explorer.py actually emits.

import type { GovernanceViewState } from "../types";

/** Frozen warning-code vocabulary → user copy. Unknown strings (human
 * messages with spaces) pass through unchanged. */
export const WARNING_COPY: Record<string, string> = {
  query_failed: "A dataset failed to load; others remain available.",
  result_truncated: "Result capped at the newest 10,000 rows — narrow filters for the full set.",
  // Treasury disclosures (governance_explorer._treasury_warning_scan).
  treasury_asof_partial:
    "A chain's newest served day is incomplete: tokens not served that day are carried from their latest served day (at most 7 days back).",
  treasury_chain_unserved: "A chain has no served treasury snapshot in the last 21 days.",
  treasury_tokens_carried:
    "Some tokens were not served on the as-of day; their balances are carried from their latest served day (at most 7 days back).",
  treasury_price_hub_stale:
    "The dbt price hub lags the treasury balances by more than two days. Values use hub prices at most 7 days old; anything older is left unpriced.",
  treasury_history_partial:
    "Some history months are partial upstream: they are drawn from what was served, and the missing tokens are named under each chart.",
  treasury_history_gap: "Some history months were not served upstream: they are left blank, never drawn as zero.",
  treasury_history_unpublished: "Some history months were never published by the census: they are left blank.",
};

/** Routine, non-actionable notices are surfaced quietly per-panel (empty
 * states) or via the FreshnessStrip STALE chip — never as a top banner. Only
 * genuine problems (a failed query, a truncated result, or a free-text error)
 * reach the compact warning strip. The quiet treasury codes are each already
 * disclosed where the number is: the toolbar's per-chain as-of chips
 * ("partial", "no served snapshot") and the history charts' GapNote + the
 * Data notes (partial / gap / unpublished months). */
export const QUIET_WARNINGS = new Set([
  "source_stale", "no_data", "stale_scope", "unsupported_choice_shape",
  "treasury_asof_partial", "treasury_chain_unserved",
  "treasury_history_partial", "treasury_history_gap", "treasury_history_unpublished",
]);

export function resolveWarnings(state: Pick<GovernanceViewState, "coverage_warnings" | "warnings">): string[] {
  return [...new Set([...(state.coverage_warnings ?? []), ...(state.warnings ?? [])])]
    .filter((warning) => !QUIET_WARNINGS.has(warning))
    .map((warning) => WARNING_COPY[warning] ?? warning);
}
