// Frontend-only view partition of the `treasury` section.
//
// Every tab reads the same two SECTION_GROUPS (core + history) the section
// loads; `groups` names which GroupGates a tab renders, so skeletons appear
// only where a tab actually waits on a group.

export type TreasuryTabId = "overview" | "assets" | "wallets" | "history";

export const DEFAULT_TREASURY_TAB: TreasuryTabId = "overview";

export interface TreasuryTab {
  id: TreasuryTabId;
  label: string;
  /** SECTION_GROUPS.treasury groups this view reads. */
  groups: readonly string[];
}

export const TREASURY_TABS: readonly TreasuryTab[] = [
  // Overview answers "how much is there, and how did it get here".
  { id: "overview", label: "Overview", groups: ["core", "history"] },
  // Assets and Wallets read history for sparklines and the by-wallet chart.
  { id: "assets", label: "Assets", groups: ["core", "history"] },
  { id: "wallets", label: "Wallets", groups: ["core", "history"] },
  { id: "history", label: "History", groups: ["history"] },
];

/** Tab ids of the previous layout, kept working for shared links. */
export const TREASURY_TAB_ALIASES: Readonly<Record<string, TreasuryTabId>> = {
  portfolio: "overview",
  tokens: "assets",
};

export function isTreasuryTab(value: unknown): value is TreasuryTabId {
  return TREASURY_TABS.some((tab) => tab.id === value);
}

/** Resolve a tab id (or an old alias); null when unrecognised. */
export function resolveTreasuryTab(value: unknown): TreasuryTabId | null {
  if (isTreasuryTab(value)) return value;
  if (typeof value === "string" && Object.prototype.hasOwnProperty.call(TREASURY_TAB_ALIASES, value)) {
    return TREASURY_TAB_ALIASES[value];
  }
  return null;
}

/** Coerce anything (a URL param, stale state) to a real tab. */
export function toTreasuryTab(value: unknown): TreasuryTabId {
  return resolveTreasuryTab(value) ?? DEFAULT_TREASURY_TAB;
}

export function groupsForTab(tab: TreasuryTabId): readonly string[] {
  return TREASURY_TABS.find((entry) => entry.id === tab)?.groups ?? ["core"];
}
