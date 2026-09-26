// When the app asks the server for the CoinGecko overlay (token icons + the
// spot FALLBACK quotes). The overlay resolves the tokens of whatever datasets
// the view holds, so it is wanted wherever treasury tokens are on screen: the
// treasury section AND the treasury wallet / token entity pages — a cold link
// to a wallet page used to render with no icons and no spot subtotal because
// the request fired only on the section.

import type { GovernanceViewState } from "../types";

export function isTreasuryEntity(state: Pick<GovernanceViewState, "section" | "selected_entity">): boolean {
  const type = state.selected_entity?.entity_type;
  return state.section === "entity" && (type === "treasury_wallet" || type === "treasury_token");
}

/** The treasury section or a treasury entity page. */
export function isTreasuryContext(state: Pick<GovernanceViewState, "section" | "selected_entity">): boolean {
  return state.section === "treasury" || isTreasuryEntity(state);
}

export function shouldRequestOverlay(
  state: Pick<GovernanceViewState, "section" | "selected_entity" | "dataset_revisions">,
): boolean {
  if (!isTreasuryContext(state)) return false;
  // Nothing to resolve until at least one dataset has landed.
  return Object.keys(state.dataset_revisions ?? {}).length > 0;
}

/** De-dup key: one overlay request per (view, dataset revisions, entity). */
export function overlayRequestKey(
  viewId: string,
  state: Pick<GovernanceViewState, "selected_entity" | "dataset_revisions">,
): string {
  const entity = state.selected_entity ? `${state.selected_entity.entity_type}:${state.selected_entity.identifier}` : "";
  return `${viewId}|${entity}|${JSON.stringify(state.dataset_revisions ?? {})}`;
}
