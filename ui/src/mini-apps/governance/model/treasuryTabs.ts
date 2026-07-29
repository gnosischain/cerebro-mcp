// Frontend-only view partition of the `treasury` section.
//
// The section loads ~13 panels and 11 charts in one scroll, which is more than
// anyone can navigate. These tabs slice that into ≤3 panels per screen WITHOUT
// changing the server contract: every tab reads the same SECTION_GROUPS the
// section already loads.
//
// `groups` is not gating. Treasury has only two non-core groups and the loader
// runs two in flight, so they already arrive together — there is no queue to
// jump. It names which GroupGates a view renders, so skeletons appear only in
// the tab that needs them, and it is exactly the set an on-demand loader would
// sync if that ever becomes worth doing.

export type TreasuryTabId = "portfolio" | "tokens" | "wallets" | "history";

export const DEFAULT_TREASURY_TAB: TreasuryTabId = "portfolio";

export interface TreasuryTab {
  id: TreasuryTabId;
  label: string;
  /** SECTION_GROUPS.treasury groups this view reads. */
  groups: readonly string[];
}

export const TREASURY_TABS: readonly TreasuryTab[] = [
  // Portfolio is first and default: it answers "how much is there", and it is
  // the only tab that needs nothing beyond `core`.
  { id: "portfolio", label: "Portfolio", groups: ["core"] },
  { id: "tokens", label: "Tokens", groups: ["core", "insights", "history"] },
  { id: "wallets", label: "Wallets", groups: ["core", "history"] },
  { id: "history", label: "History", groups: ["history"] },
];

export function isTreasuryTab(value: unknown): value is TreasuryTabId {
  return TREASURY_TABS.some((tab) => tab.id === value);
}

/** Coerce anything (a URL param, stale state) to a real tab. */
export function toTreasuryTab(value: unknown): TreasuryTabId {
  return isTreasuryTab(value) ? value : DEFAULT_TREASURY_TAB;
}

export function groupsForTab(tab: TreasuryTabId): readonly string[] {
  return TREASURY_TABS.find((entry) => entry.id === tab)?.groups ?? ["core"];
}
