// Pure seed / navigation helpers, extracted from the app wiring so tests can
// exercise them without rendering. Every helper returns a "tool call" record
// shaped for the serialized loader: `{ __tool, ...args }` where `request_id`
// is a placeholder the loader overwrites with its own monotonic id.

import { isFeeBand, type PlxListSection, type PoolsExplorerViewState } from "../types";
import { daysToWindow, type PlxUrlState } from "../urlState";
import { isListSection } from "../model/navGroups";
import {
  EMPTY_DRAFT, buildEntityArgs, buildSectionToolArgs, draftFromState, type PlxFilterDraft,
} from "./toolArgs";

export type PlxToolCall = Record<string, unknown> & { __tool: string };

/** Map the one-shot URL seed onto a filter draft. `fee` is a band id or an
 * exact pip count; the two stay mutually exclusive. */
export function draftFromSeed(seed: PlxUrlState): PlxFilterDraft {
  const band = isFeeBand(seed.fee) ? seed.fee : "";
  const fee = band ? 0 : Math.max(0, Math.floor(Number(seed.fee) || 0));
  return {
    ...EMPTY_DRAFT,
    query: seed.q,
    pool_class: seed.class,
    pool_family: seed.family,
    fee_band: band,
    fee,
    token: seed.tok,
    live_only: seed.live,
    probed_only: seed.probed,
    sort_by: seed.sort,
  };
}

/** Section named by the seed, falling back when absent/unknown. */
export function sectionFromSeed(seed: PlxUrlState | null, fallback: PlxListSection): PlxListSection {
  return seed && isListSection(seed.section) ? seed.section : fallback;
}

/** The FIRST load the deferred-load driver issues, given the (possibly null)
 * one-shot URL seed and the server's opening state:
 *   - a URL `entity`+`id` pair short-circuits to the entity load;
 *   - an opener that already selected an entity (open_pools_explorer with
 *     entity_type/identifier) loads that entity — the open is zero-query;
 *   - anything else applies the seeded (or opened) list section with the
 *     seed-derived draft, as_of and window. */
export function seedCall(
  viewId: string,
  state: PoolsExplorerViewState,
  seed: PlxUrlState | null,
): PlxToolCall {
  const seedWindow = seed && seed.days !== null ? daysToWindow(seed.days) : "";
  const asOf = seed?.date || state.as_of || "";
  const window = seedWindow || state.window || "";
  if (seed && seed.entity && seed.id) {
    return buildEntityArgs(viewId, seed.entity, seed.id, { asOf, window }) as PlxToolCall;
  }
  if ((state.section === "pool" || state.section === "token") && state.selected_entity) {
    return buildEntityArgs(viewId, state.selected_entity.entity_type, state.selected_entity.identifier, {
      asOf: state.as_of || "",
      window: state.window || "",
    }) as PlxToolCall;
  }
  const fallback: PlxListSection = isListSection(state.section) ? state.section : "overview";
  const section = sectionFromSeed(seed, fallback);
  const draft = seed ? draftFromSeed(seed) : draftFromState(state);
  return buildSectionToolArgs(viewId, section, draft, { asOf, window }) as PlxToolCall;
}
