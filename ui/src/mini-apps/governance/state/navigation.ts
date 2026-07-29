// Pure breadcrumb-trail and URL-deep-link helpers, extracted from the app
// wiring so breadcrumbs.test.ts can exercise them without rendering.
//
// Every helper returns a "tool call" record shaped for the app's serialized
// loader: `{ __tool, ...args }` where `request_id` is a placeholder the
// loader overwrites with its own monotonic id.

import type { GovBreadcrumb, GovEntityType, GovSection } from "../types";
import type { GovUrlState } from "../urlState";
import {
  buildEntityArgs,
  buildSectionToolArgs,
  EMPTY_DRAFT,
  type GovFilterDraft,
} from "./toolArgs";

export const BREADCRUMB_CAP = 8;

export type GovToolCall = Record<string, unknown> & { __tool: string };

export type GovSectionId = Exclude<GovSection, "entity">;

const SECTION_IDS = new Set<string>([
  "overview", "proposals", "voters", "forum", "delegations", "treasury",
]);
const ENTITY_TYPES = new Set<string>([
  "proposal", "voter", "forum_topic", "forum_user",
  "treasury_token", "treasury_wallet",
]);

export function isGovSectionId(value: string): value is GovSectionId {
  return SECTION_IDS.has(value);
}

export function isGovEntityType(value: string): value is GovEntityType {
  return ENTITY_TYPES.has(value);
}

/** Display trail: dedupe by entity identity keeping the NEWEST occurrence of
 * each entity (a revisited entity moves to its latest position), then keep
 * only the newest `cap` chips. The server also caps its own trail — this is
 * the defensive client mirror. */
export function trailForDisplay(
  crumbs: GovBreadcrumb[],
  cap: number = BREADCRUMB_CAP,
): GovBreadcrumb[] {
  const seen = new Set<string>();
  const out: GovBreadcrumb[] = [];
  for (let i = crumbs.length - 1; i >= 0; i -= 1) {
    const crumb = crumbs[i];
    const key = `${crumb.entity_type}:${crumb.identifier}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.unshift(crumb);
  }
  return out.slice(Math.max(0, out.length - cap));
}

/** A drill-down to one entity (breadcrumb chip, table click, feed click). */
export function entityCall(
  viewId: string,
  entityType: GovEntityType,
  identifier: string,
): GovToolCall {
  return {
    __tool: "load_governance_entity",
    ...buildEntityArgs(viewId, 0, entityType, identifier),
  };
}

/** Breadcrumb chip click → reload that entity. */
export function crumbCall(viewId: string, crumb: GovBreadcrumb): GovToolCall {
  return entityCall(viewId, crumb.entity_type, crumb.identifier);
}

/** Leading breadcrumb chip → return to the section the user drilled in from. */
export function sectionReturnCall(
  viewId: string,
  section: GovSectionId,
  draft: GovFilterDraft,
  forceRefresh?: boolean,
): GovToolCall {
  return {
    __tool: "load_governance_section",
    ...buildSectionToolArgs(viewId, 0, section, draft, forceRefresh),
  };
}

/** Map the one-shot URL seed onto a filter draft (frozen `days` semantics:
 * 90 / 365 presets, 0 explicit all-history, start+end pair = custom). A
 * half-specified custom range degrades to all-history. */
export function draftFromSeed(seed: GovUrlState): GovFilterDraft {
  const custom = Boolean(seed.start && seed.end);
  const days = custom
    ? null
    : seed.days === 90 || seed.days === 365 || seed.days === 0
      ? seed.days
      : 0;
  return {
    ...EMPTY_DRAFT,
    days,
    start: custom ? seed.start : "",
    end: custom ? seed.end : "",
    query: seed.q,
    proposal_state: seed.pstate,
    proposal_type: seed.ptype,
    quorum_status: seed.quorum,
    category_id: seed.cat,
    forum_status: seed.fstatus,
    sort_by: seed.sort,
  };
}

/** Section named by the seed, falling back when absent/unknown/"entity". */
export function sectionFromSeed(
  seed: GovUrlState,
  fallback: GovSectionId,
): GovSectionId {
  return isGovSectionId(seed.section) ? seed.section : fallback;
}

/** The FIRST load the deferred-load driver issues, given the (possibly null)
 * one-shot URL seed. An `entity`+`id` pair short-circuits to the entity
 * load; anything else applies the seeded (or fallback) section with the
 * seed-derived draft. */
export function seedCall(
  viewId: string,
  seed: GovUrlState | null,
  fallbackSection: GovSectionId,
  currentDraft: GovFilterDraft,
): GovToolCall {
  if (seed && seed.entity && seed.id && isGovEntityType(seed.entity)) {
    return entityCall(viewId, seed.entity, seed.id);
  }
  if (!seed) return sectionReturnCall(viewId, fallbackSection, currentDraft);
  return sectionReturnCall(
    viewId,
    sectionFromSeed(seed, fallbackSection),
    draftFromSeed(seed),
  );
}
