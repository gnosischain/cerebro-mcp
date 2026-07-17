// Module → display sector mapping shared by the Atlas rail and the
// investigate-mode profile chips.

import type { ProfileCard } from "../types";

const SECTOR_LABELS: Record<string, string> = {
  Circles: "Circles",
  circles: "Circles",
  gpay: "GPay",
  safe: "Safe",
  transfers: "Transfers",
  pools: "Pools",
  yields: "Yields",
  consensus: "Staking",
  GBCDeposit: "Staking",
  bridges: "Bridges",
  crawlers_data: "Labels",
  shared: "Shared",
};

export const SECTOR_COLOR: Record<string, string> = {
  Circles: "#60a5fa",
  GPay: "#fbbf24",
  Safe: "#a78bfa",
  Transfers: "#6ee7b7",
  Pools: "#c084fc",
  Yields: "#f472b6",
  Staking: "#f97316",
  Bridges: "#facc15",
  Labels: "#94a3b8",
  Shared: "#94a3b8",
};

export function sectorOf(module: string): string {
  return SECTOR_LABELS[module] ?? module ?? "Other";
}

export function groupProfilesBySector(
  profiles: ProfileCard[],
): Array<[string, ProfileCard[]]> {
  const out: Record<string, ProfileCard[]> = {};
  for (const profile of profiles) {
    const sector = sectorOf(profile.module);
    (out[sector] ||= []).push(profile);
  }
  for (const list of Object.values(out)) {
    list.sort((a, b) => a.profile.localeCompare(b.profile));
  }
  return Object.entries(out).sort(([a], [b]) => a.localeCompare(b));
}
