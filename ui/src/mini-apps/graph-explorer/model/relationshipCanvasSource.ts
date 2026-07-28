// Which rows does the Relationships canvas draw, and is that drawing
// trustworthy yet?
//
// Relationships used to be two modes. Catalog (Atlas) resolved its canvas from
// `previewProfile ? previewModel : appliedModel`, where `previewModel` was
// built from EMPTY ARRAYS unless a five-way scope agreement held. Any in-flight
// request, descriptor lag or scope mismatch therefore produced an empty model —
// and an empty model hid the canvas chrome, so the user lost the data and every
// control that could recover it at the same moment.
//
// This module makes that resolution explicit, total and pure: exactly one
// source wins, and a source that is not yet trustworthy reports WHY instead of
// silently degrading to zero rows. The scope agreement is unchanged — it is the
// thing that keeps a stale hydration from being rendered as if it answered the
// current question — but it now selects a *state*, not an empty array.

import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import type { ForensicScope } from "../types";

export type RelationshipSourceKind = "seed" | "preview" | "sample" | "empty";

/** Why a source is not currently drawable. `null` means it is. */
export type RelationshipBlocker =
  | { reason: "loading"; detail: string }
  | { reason: "failed"; detail: string }
  | { reason: "stale-scope"; detail: string }
  | { reason: "no-rows"; detail: string }
  | { reason: "nothing-chosen"; detail: string };

export interface RelationshipCanvasSource {
  kind: RelationshipSourceKind;
  /** Rows to render. Empty when `blocker` is set. */
  nodeRows: unknown[][];
  edgeRows: unknown[][];
  /** Profiles that scope the model build. */
  profiles: string[];
  /** The scope backing these rows, when one is trustworthy. */
  scope: ForensicScope | undefined;
  /** Set when the rows are not drawable; drives the canvas empty state. */
  blocker: RelationshipBlocker | null;
  /** True while the rows on screen are known to lag the user's intent. The
   * canvas dims but KEEPS its chrome — controls must never vanish with data. */
  stale: boolean;
  /** Preview only: the profile being inspected. */
  previewProfile: string;
}

interface DescriptorLike {
  scope_id?: string | null;
  stats?: { row_count?: number };
  preview_rows?: unknown[][];
}

/**
 * Every dataset in a group must carry the SAME scope_id, in all three places
 * it is published: the group scope, the view-level `dataset_scopes` map, and
 * each dataset descriptor. Disagreement means a hydration is mid-flight or a
 * previous answer is still mounted, and neither may be drawn as current.
 */
export function scopeAgrees(
  scopeId: string,
  datasetScopes: Record<string, string> | undefined,
  datasetNames: string[],
  descriptors: (DescriptorLike | undefined)[],
): boolean {
  if (!scopeId) return false;
  for (const name of datasetNames) {
    if (datasetScopes?.[name] !== scopeId) return false;
  }
  for (const descriptor of descriptors) {
    if (descriptor?.scope_id !== scopeId) return false;
  }
  return true;
}

/** A descriptor whose inline page is short of its own row_count is still
 * hydrating; its rows are a prefix, not the answer. */
export function descriptorIncomplete(descriptor: DescriptorLike | undefined): boolean {
  if (!descriptor) return false;
  return (
    (descriptor.stats?.row_count ?? 0) > (descriptor.preview_rows?.length ?? 0)
  );
}

export interface ResolveInput {
  /** Investigate seed (server.investigate.seed.id). */
  seedId: string;
  seedScope: ForensicScope | undefined;
  seedNodeRows: unknown[][] | undefined;
  seedEdgeRows: unknown[][] | undefined;
  seedProfiles: string[];
  seedLoading: boolean;
  seedError: string | null;
  /** True when the seed's applied controls differ from the local draft. */
  seedControlsStale: boolean;

  /** Catalog sample (atlas_nodes / atlas_edges). */
  sampleScope: ForensicScope | undefined;
  sampleNodeRows: unknown[][] | undefined;
  sampleEdgeRows: unknown[][] | undefined;
  sampleNodeDescriptor: DatasetDescriptor | undefined;
  sampleEdgeDescriptor: DatasetDescriptor | undefined;
  sampleProfiles: string[];
  sampleLoading: boolean;
  sampleError: string | null;
  sampleDraftStale: boolean;

  /** Inspect-only preview of one profile. */
  previewProfile: string;
  previewStateProfile: string;
  previewScope: ForensicScope | undefined;
  previewRequestId: number;
  desiredPreviewRequestId: number;
  previewNodeDescriptor: DatasetDescriptor | undefined;
  previewEdgeDescriptor: DatasetDescriptor | undefined;
  previewDatasetError: string | null;
  previewLoading: boolean;
  previewError: string | null;

  /** server.dataset_scopes */
  datasetScopes: Record<string, string> | undefined;
}

const EMPTY: unknown[][] = [];

/**
 * Resolve the single source the canvas draws.
 *
 * Precedence: preview (an explicit click) > seed (a loaded investigation) >
 * catalog sample > nothing. Preview wins over seed because entering preview is
 * a deliberate act; leaving it restores the seed underneath.
 */
export function resolveCanvasSource(input: ResolveInput): RelationshipCanvasSource {
  if (input.previewProfile) return resolvePreview(input);
  if (input.seedId) return resolveSeed(input);
  if (input.sampleProfiles.length) return resolveSample(input);
  return {
    kind: "empty",
    nodeRows: EMPTY,
    edgeRows: EMPTY,
    profiles: [],
    scope: undefined,
    blocker: {
      reason: "nothing-chosen",
      detail:
        "Pick a relationship type on the left, or start from an address, to draw a graph.",
    },
    stale: false,
    previewProfile: "",
  };
}

function resolvePreview(input: ResolveInput): RelationshipCanvasSource {
  const scopeId = String(input.previewScope?.scope_id ?? "");
  // The same five-way agreement the old AtlasView used, unchanged.
  const matchesIntent = Boolean(
    input.previewStateProfile === input.previewProfile &&
      Number(input.previewRequestId ?? -1) === input.desiredPreviewRequestId &&
      scopeAgrees(
        scopeId,
        input.datasetScopes,
        ["atlas_preview_nodes", "atlas_preview_edges"],
        [input.previewNodeDescriptor, input.previewEdgeDescriptor],
      ),
  );
  const incomplete =
    matchesIntent &&
    (descriptorIncomplete(input.previewNodeDescriptor) ||
      descriptorIncomplete(input.previewEdgeDescriptor));
  const problem =
    (!input.previewLoading ? input.previewError : null) ||
    input.previewDatasetError ||
    (matchesIntent && input.previewScope?.status === "failed"
      ? "The answering relation failed validation or query execution."
      : null);

  const base = {
    kind: "preview" as const,
    profiles: [input.previewProfile],
    previewProfile: input.previewProfile,
  };

  if (problem) {
    return {
      ...base,
      nodeRows: EMPTY,
      edgeRows: EMPTY,
      scope: matchesIntent ? input.previewScope : undefined,
      blocker: { reason: "failed", detail: problem },
      stale: true,
    };
  }
  if (input.previewLoading || incomplete || !matchesIntent) {
    return {
      ...base,
      nodeRows: EMPTY,
      edgeRows: EMPTY,
      scope: undefined,
      blocker: {
        reason: matchesIntent ? "loading" : "stale-scope",
        detail: `Loading a real ${input.previewProfile} sample…`,
      },
      stale: true,
    };
  }

  const nodeRows = input.previewNodeDescriptor?.preview_rows ?? EMPTY;
  const edgeRows = input.previewEdgeDescriptor?.preview_rows ?? EMPTY;
  return {
    ...base,
    nodeRows,
    edgeRows,
    scope: input.previewScope,
    blocker: edgeRows.length
      ? null
      : {
          reason: "no-rows",
          detail:
            "The answering relation returned no rows for this preview scope.",
        },
    stale: false,
  };
}

function resolveSeed(input: ResolveInput): RelationshipCanvasSource {
  const base = {
    kind: "seed" as const,
    profiles: input.seedProfiles,
    previewProfile: "",
  };
  if (input.seedError) {
    return {
      ...base,
      nodeRows: EMPTY,
      edgeRows: EMPTY,
      scope: input.seedScope,
      blocker: { reason: "failed", detail: input.seedError },
      stale: true,
    };
  }
  const nodeRows = input.seedNodeRows ?? EMPTY;
  const edgeRows = input.seedEdgeRows ?? EMPTY;
  // A seed with no rows is a real answer ("nothing in this window"), not a
  // stale scope — the seed itself came back. Say so precisely.
  return {
    ...base,
    nodeRows,
    edgeRows,
    scope: input.seedScope,
    blocker:
      edgeRows.length || nodeRows.length
        ? null
        : {
            reason: input.seedLoading ? "loading" : "no-rows",
            detail: input.seedLoading
              ? "Loading the neighbourhood…"
              : "No relationships for this seed in the applied window. Widen the window, add relationship types, or raise the neighbour cap.",
          },
    stale: input.seedControlsStale,
  };
}

function resolveSample(input: ResolveInput): RelationshipCanvasSource {
  const scopeId = String(input.sampleScope?.scope_id ?? "");
  // Gated on the SAME agreement as preview. Gating on array non-emptiness
  // instead would render a previous profile selection's rows as if they
  // answered the current one.
  const agrees = scopeAgrees(
    scopeId,
    input.datasetScopes,
    ["atlas_nodes", "atlas_edges"],
    [input.sampleNodeDescriptor, input.sampleEdgeDescriptor],
  );
  const base = {
    kind: "sample" as const,
    profiles: input.sampleProfiles,
    previewProfile: "",
  };
  if (input.sampleError) {
    return {
      ...base,
      nodeRows: EMPTY,
      edgeRows: EMPTY,
      scope: input.sampleScope,
      blocker: { reason: "failed", detail: input.sampleError },
      stale: true,
    };
  }
  if (input.sampleLoading || !agrees) {
    return {
      ...base,
      nodeRows: EMPTY,
      edgeRows: EMPTY,
      scope: undefined,
      blocker: {
        reason: input.sampleLoading ? "loading" : "stale-scope",
        detail: "Loading a sample of the selected relationship types…",
      },
      stale: true,
    };
  }
  const nodeRows = input.sampleNodeRows ?? EMPTY;
  const edgeRows = input.sampleEdgeRows ?? EMPTY;
  return {
    ...base,
    nodeRows,
    edgeRows,
    scope: input.sampleScope,
    blocker: edgeRows.length
      ? null
      : {
          reason: "no-rows",
          detail:
            "These relationship types returned no rows in the applied window.",
        },
    stale: input.sampleDraftStale,
  };
}
