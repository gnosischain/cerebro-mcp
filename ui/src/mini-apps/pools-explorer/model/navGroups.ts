// Navigation model: the four list sections, the pool-detail tabs (a
// frontend-only partition of the `pool` section's groups) and the profile
// view toggle. Balancer (reserves_only) pools expose only Reserves and
// Publication; state-only CL pools keep every tab but render a stub where the
// probe-dependent datasets are empty (detail/PoolDetail.tsx).

import type { PlxEntityType, PlxListSection, PlxSection } from "../types";

export const SECTIONS: ReadonlyArray<{ id: PlxListSection; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "pools", label: "Pools" },
  { id: "tokens", label: "Tokens" },
  { id: "coverage", label: "Coverage" },
];

const LIST_SECTION_IDS = new Set<string>(SECTIONS.map((section) => section.id));
const ENTITY_TYPES = new Set<string>(["pool", "token"]);

export function isListSection(value: unknown): value is PlxListSection {
  return typeof value === "string" && LIST_SECTION_IDS.has(value);
}

export function isEntityType(value: unknown): value is PlxEntityType {
  return typeof value === "string" && ENTITY_TYPES.has(value);
}

export function isPlxSection(value: unknown): value is PlxSection {
  return isListSection(value) || isEntityType(value);
}

export type PoolTabId = "profile" | "history" | "reserves" | "fees" | "ticks" | "publication";

export interface PoolTab {
  id: PoolTabId;
  label: string;
  /** SECTION_GROUPS.pool groups this tab reads (skeleton gating). */
  groups: readonly string[];
  /** Concentrated-liquidity pools only. */
  clOnly: boolean;
  /** Needs `ticks_probed = 1`; state-only CL pools render a stub instead. */
  needsProbe: boolean;
}

export const POOL_TABS: readonly PoolTab[] = [
  { id: "profile", label: "Profile", groups: ["profile"], clOnly: true, needsProbe: true },
  { id: "history", label: "History", groups: ["history"], clOnly: true, needsProbe: false },
  { id: "reserves", label: "Reserves", groups: ["history"], clOnly: false, needsProbe: false },
  { id: "fees", label: "Fees", groups: ["fees"], clOnly: true, needsProbe: true },
  { id: "ticks", label: "Ticks", groups: ["profile"], clOnly: true, needsProbe: true },
  { id: "publication", label: "Publication", groups: ["core"], clOnly: false, needsProbe: false },
];

export const DEFAULT_POOL_TAB: PoolTabId = "profile";

export function isPoolTab(value: unknown): value is PoolTabId {
  return POOL_TABS.some((tab) => tab.id === value);
}

/** Tabs a pool of this family exposes. */
export function tabsForPool(family: string | null | undefined): readonly PoolTab[] {
  if (family === "reserves_only") return POOL_TABS.filter((tab) => !tab.clOnly);
  return POOL_TABS;
}

/** Coerce anything (a URL param, stale state) to a tab this pool exposes. */
export function coercePoolTab(value: unknown, family: string | null | undefined): PoolTabId {
  const tabs = tabsForPool(family);
  if (tabs.some((tab) => tab.id === value)) return value as PoolTabId;
  return tabs[0]?.id ?? DEFAULT_POOL_TAB;
}

export type ProfileView = "snapshot" | "over_time";
export const DEFAULT_PROFILE_VIEW: ProfileView = "snapshot";
export function isProfileView(value: unknown): value is ProfileView {
  return value === "snapshot" || value === "over_time";
}
