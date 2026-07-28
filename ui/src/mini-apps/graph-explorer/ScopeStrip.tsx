// One line, always present, saying what is on the canvas and how much of it.
//
// It replaces four separate strips that used to appear and disappear
// independently — `.ge-applied-scope-chip`, `.ge-pending-chip`,
// `.ge-status-filter-note` and the per-mode prose header. Each was honest on
// its own, but together they made the view jump as state changed and pushed
// the graph down the page. A fixed one-line strip means the reader always
// looks in the same place, and the canvas keeps its height.

import { ChainBadge } from "../shared/ChainBadge";
import { coverageLabel } from "./model/profileMeta";
import type { RelationshipCanvasSource } from "./model/relationshipCanvasSource";
import type { ForensicScope, StatusFilter } from "./types";

interface Props {
  source: RelationshipCanvasSource;
  scope: ForensicScope | undefined;
  windowDays: number;
  /** Applied window, when it differs from the local draft. */
  appliedWindowDays: number | null;
  statusFilter: StatusFilter;
  /** Active profiles hidden from the canvas by the status filter. */
  trimmedCount: number;
  onClearStatusFilter: () => void;
  onLeavePreview: () => void;
}

function sourceLabel(source: RelationshipCanvasSource): string {
  switch (source.kind) {
    case "seed":
      return "Neighbourhood of the seed";
    case "preview":
      return `Preview only · ${source.previewProfile}`;
    case "sample":
      return `Sample of ${source.profiles.length} relationship type${source.profiles.length === 1 ? "" : "s"}`;
    default:
      return "Nothing selected";
  }
}

export function ScopeStrip({
  source,
  scope,
  windowDays,
  appliedWindowDays,
  statusFilter,
  trimmedCount,
  onClearStatusFilter,
  onLeavePreview,
}: Props) {
  const chainId = scope?.chain_id;
  return (
    <div className={`ge-scope-strip is-${source.kind}`} role="status">
      <span className="ge-scope-strip__what">{sourceLabel(source)}</span>

      {source.kind === "preview" ? (
        <>
          <span className="ge-scope-strip__note">
            not part of the applied graph
          </span>
          <button type="button" className="ge-btn" onClick={onLeavePreview}>
            Back to applied graph
          </button>
        </>
      ) : null}

      <span className="ge-scope-strip__window">
        {appliedWindowDays != null && appliedWindowDays !== windowDays
          ? `applied ${appliedWindowDays}d (draft ${windowDays}d)`
          : `${windowDays}d window`}
      </span>

      {/* Coverage is reported, never inferred: an unknown total stays unknown
          rather than being rendered as "all of them". */}
      <span className="ge-scope-strip__coverage">
        {scope ? coverageLabel("Edges", scope.coverage?.edges) : "coverage unknown"}
      </span>

      {source.stale ? (
        <span className="ge-pending-chip">
          {source.blocker?.reason === "loading" ? "loading" : "showing applied results"}
        </span>
      ) : null}

      {trimmedCount > 0 ? (
        <button
          type="button"
          className="ge-scope-strip__filter"
          onClick={onClearStatusFilter}
          title="Status filter hides some active profiles from the canvas"
        >
          {statusFilter} filter hides {trimmedCount} — show all
        </button>
      ) : null}

      {chainId != null ? (
        <span className="ge-scope-strip__chain">
          <ChainBadge chainId={chainId} />
        </span>
      ) : null}
    </div>
  );
}
