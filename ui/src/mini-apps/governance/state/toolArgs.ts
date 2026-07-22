// Tool-argument builders for the Governance Explorer. FROZEN date-token
// contract: `load_governance_section` has NO window_days parameter —
//
//   days 0            -> start_at ""    end_at ""   (all history, default)
//   days 90           -> start_at "90d" end_at ""   (relative, now()-anchored)
//   days 365          -> start_at "1y"  end_at ""
//   custom (days=null)-> start_at ISO   end_at ISO  (pair required)
//
// `force_refresh` is emitted ONLY when explicitly passed (the Refresh
// button); routine applies never carry it.

import type { GovEntityType, GovSection } from "../types";

export interface GovFilterDraft {
  /** 0 = all history, 90 / 365 = relative presets, null = custom ISO pair. */
  days: number | null;
  start: string;
  end: string;
  query: string;
  proposal_state: string;
  proposal_type: string;
  quorum_status: string;
  category_id: number;
  forum_status: string;
  sort_by: string;
}

export const EMPTY_DRAFT: GovFilterDraft = {
  days: 0,
  start: "",
  end: "",
  query: "",
  proposal_state: "",
  proposal_type: "",
  quorum_status: "",
  category_id: 0,
  forum_status: "",
  sort_by: "",
};

function encodeRange(draft: GovFilterDraft): { start_at: string; end_at: string } {
  if (draft.days === 90) return { start_at: "90d", end_at: "" };
  if (draft.days === 365) return { start_at: "1y", end_at: "" };
  if (draft.days === null && draft.start && draft.end) {
    return { start_at: draft.start, end_at: draft.end };
  }
  return { start_at: "", end_at: "" };
}

export function buildSectionToolArgs(
  viewId: string,
  requestId: number,
  section: Exclude<GovSection, "entity">,
  draft: GovFilterDraft,
  forceRefresh?: boolean,
): Record<string, unknown> {
  const args: Record<string, unknown> = {
    view_id: viewId,
    request_id: requestId,
    section,
    query: draft.query,
    ...encodeRange(draft),
    proposal_state: draft.proposal_state,
    proposal_type: draft.proposal_type,
    quorum_status: draft.quorum_status,
    category_id: draft.category_id,
    forum_status: draft.forum_status,
    sort_by: draft.sort_by,
  };
  if (forceRefresh !== undefined) args.force_refresh = forceRefresh;
  return args;
}

export function buildEntityArgs(
  viewId: string,
  requestId: number,
  entityType: GovEntityType,
  identifier: string,
): Record<string, unknown> {
  return {
    view_id: viewId,
    request_id: requestId,
    entity_type: entityType,
    identifier,
  };
}

export function buildSearchArgs(
  viewId: string,
  requestId: number,
  query: string,
): Record<string, unknown> {
  return { view_id: viewId, request_id: requestId, query };
}
