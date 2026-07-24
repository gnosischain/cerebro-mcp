// Grouped two-level navigation model. The SERVER keeps its flat 9 sections;
// the frontend groups them and adds FACET destinations — client-only views
// over an existing server section that just pre-sync a specific set of that
// section's deferred dataset groups and (later) render a dedicated component.

import type { CowFacet, CowSection } from "../types";

/** Everything the nav can point at: a real server section or a facet. */
export type CowNavDestination = Exclude<CowSection, "entity"> | CowFacet;

export interface CowNavEntry {
  id: CowNavDestination;
  label: string;
}

export interface CowNavGroup {
  id: "live" | "overview" | "trading" | "solvers" | "traders";
  label: string;
  /** First entry is the group's default destination (seed for last-visited). */
  destinations: CowNavEntry[];
}

export const NAV_GROUPS: CowNavGroup[] = [
  { id: "live", label: "Live", destinations: [{ id: "live", label: "Live" }] },
  { id: "overview", label: "Overview", destinations: [{ id: "overview", label: "Overview" }] },
  {
    id: "trading",
    label: "Trading",
    destinations: [
      { id: "markets", label: "Markets" },
      { id: "trades", label: "Trades" },
      { id: "orders", label: "Orders" },
      { id: "order_types", label: "Order types" },
    ],
  },
  {
    id: "solvers",
    label: "Solvers",
    destinations: [
      { id: "auctions", label: "Auctions" },
      { id: "solvers", label: "Analytics" },
      { id: "solver_directory", label: "Directory" },
      { id: "patterns", label: "Patterns" },
    ],
  },
  {
    id: "traders",
    label: "Traders",
    destinations: [
      { id: "traders", label: "Leaderboard" },
      { id: "trader_dynamics", label: "Dynamics" },
    ],
  },
];

export interface FacetView {
  /** Host server section the facet renders over (and loads through). */
  section: Exclude<CowSection, "entity">;
  /** Deferred dataset groups of the host section the facet needs synced. */
  groups: string[];
}

export const FACET_VIEWS: Record<CowFacet, FacetView> = {
  order_types: { section: "orders", groups: ["types", "programmatic", "class_quality"] },
  solver_directory: { section: "solvers", groups: ["directory"] },
  trader_dynamics: { section: "traders", groups: ["dynamics", "retention"] },
};

export const FACET_IDS = Object.keys(FACET_VIEWS) as CowFacet[];

export function isCowFacet(value: string): value is CowFacet {
  return Object.prototype.hasOwnProperty.call(FACET_VIEWS, value);
}

/** destination id → owning nav-group id (derived; totality is unit-tested). */
export const SECTION_TO_GROUP: Record<CowNavDestination, CowNavGroup["id"]> =
  Object.fromEntries(
    NAV_GROUPS.flatMap((group) => group.destinations.map((entry) => [entry.id, group.id])),
  ) as Record<CowNavDestination, CowNavGroup["id"]>;

export interface ResolvedDestination {
  /** Server section to apply / render through. */
  section: Exclude<CowSection, "entity">;
  /** Non-null when the destination is a facet. */
  facet: CowFacet | null;
  /** Facet's dataset groups to sync; empty for plain sections (the standard
   * deferred-load driver streams a section's own groups). */
  groups: string[];
}

export function resolveDestination(dest: CowNavDestination): ResolvedDestination {
  if (isCowFacet(dest)) {
    const view = FACET_VIEWS[dest];
    return { section: view.section, facet: dest, groups: view.groups };
  }
  return { section: dest, facet: null, groups: [] };
}

/** Prop contract for threading the active facet into SectionViews.
 * FACET-HOOK: sections/SectionViews.tsx does not declare `facet` in its Props
 * yet — the sections agent should extend Props with this interface and, when
 * `facet` is non-null (its host section always equals the rendered
 * `state.section` by construction), dispatch the facet component
 * (order_types / solver_directory / trader_dynamics) instead of the host
 * section's default view. Until then the prop is silently ignored and the
 * host section's normal view renders as the fallback. */
export interface FacetHostProps {
  facet?: CowFacet | null;
}
