// Investigate-mode topbar: window / max-neighbors (debounced refetch by the
// parent), layout toggle, semantic-status filter, expand-depth stepper (reads
// the SERVER-published limits — no compile-time hop cap), and the explicit
// "+ Expand" button. Expansion happens ONLY on explicit action (this button
// or a canvas double-click) — never silently on stepper change (WS10-F).

import type { GraphLayout, Limits, StatusFilter } from "./types";

interface Props {
  windowDays: number;
  maxNeighbors: number;
  layout: GraphLayout;
  statusFilter: StatusFilter;
  expandDepth: number;
  limits: Limits;
  /** "selected node" or "seed" — surfaced on the expand button. */
  expandTarget: string;
  canExpand: boolean;
  detailsOpen: boolean;
  onWindowChange: (days: number) => void;
  onMaxNeighborsChange: (value: number) => void;
  onLayoutChange: (layout: GraphLayout) => void;
  onStatusFilterChange: (filter: StatusFilter) => void;
  onExpandDepthChange: (depth: number) => void;
  onExpand: () => void;
  onToggleDetails: () => void;
}

export function FilterBar({
  windowDays,
  maxNeighbors,
  layout,
  statusFilter,
  expandDepth,
  limits,
  expandTarget,
  canExpand,
  detailsOpen,
  onWindowChange,
  onMaxNeighborsChange,
  onLayoutChange,
  onStatusFilterChange,
  onExpandDepthChange,
  onExpand,
  onToggleDetails,
}: Props) {
  const maxHops = Math.max(1, limits.max_hops);
  return (
    <header className="ge-topbar">
      <div className="ge-topbar-filters">
        <label className="ge-pill" title="Time window (days)">
          <span className="ge-pill-icon" aria-hidden>🕑</span>
          <input
            type="number"
            min={1}
            max={3650}
            value={windowDays}
            onChange={(e) => onWindowChange(Math.max(1, Number(e.target.value) || 0))}
          />
          <span className="ge-pill-unit">d</span>
        </label>

        <label className="ge-pill" title="Max neighbors per hop">
          <span className="ge-pill-icon" aria-hidden>◎</span>
          <input
            type="number"
            min={1}
            max={2000}
            value={maxNeighbors}
            onChange={(e) =>
              onMaxNeighborsChange(Math.max(1, Number(e.target.value) || 0))
            }
          />
        </label>

        <div className="ge-segment" role="tablist" aria-label="Layout">
          <button
            type="button"
            className={layout === "force" ? "active" : ""}
            onClick={() => onLayoutChange("force")}
            title="Force layout"
          >
            Force
          </button>
          <button
            type="button"
            className={layout === "circular" ? "active" : ""}
            onClick={() => onLayoutChange("circular")}
            title="Circular layout"
          >
            Circle
          </button>
        </div>

        <div className="ge-segment ge-segment-status" role="tablist" aria-label="Semantic status">
          <button
            type="button"
            className={statusFilter === "all" ? "active" : ""}
            onClick={() => onStatusFilterChange("all")}
            title="Show all profiles"
          >
            All
          </button>
          <button
            type="button"
            aria-label="Show approved profiles only"
            className={"appr " + (statusFilter === "approved" ? "active" : "")}
            onClick={() => onStatusFilterChange("approved")}
            title="Approved only"
          >
            <span className="ge-dot ge-dot-approved" aria-hidden />
            <span className="sr-only">Approved</span>
          </button>
          <button
            type="button"
            aria-label="Show candidate profiles only"
            className={"cand " + (statusFilter === "candidate" ? "active" : "")}
            onClick={() => onStatusFilterChange("candidate")}
            title="Candidate only"
          >
            <span className="ge-dot ge-dot-candidate" aria-hidden />
            <span className="sr-only">Candidate</span>
          </button>
        </div>
      </div>

      <div className="ge-topbar-right">
        <div
          className="ge-pill ge-stepper"
          title={`BFS depth — how many frontier rounds the expand button (and double-click) add. Server cap: ${maxHops}.`}
        >
          <button
            type="button"
            className="ge-stepper-btn"
            onClick={() => onExpandDepthChange(expandDepth - 1)}
            disabled={expandDepth <= 1}
            aria-label="Decrease expand depth"
          >
            −
          </button>
          <input
            type="number"
            min={1}
            max={maxHops}
            value={expandDepth}
            onChange={(e) => onExpandDepthChange(Number(e.target.value) || 1)}
            aria-label="Expand depth (hops)"
            style={{ width: 28 }}
          />
          <button
            type="button"
            className="ge-stepper-btn"
            onClick={() => onExpandDepthChange(expandDepth + 1)}
            disabled={expandDepth >= maxHops}
            aria-label="Increase expand depth"
          >
            +
          </button>
          <span className="ge-pill-unit">hop</span>
        </div>
        <button
          type="button"
          className="ge-btn primary ge-expand-btn"
          onClick={onExpand}
          title={`Expand the ${expandTarget} by ${expandDepth} hop${expandDepth === 1 ? "" : "s"} (BFS frontier rounds)`}
          disabled={!canExpand}
        >
          + Expand {expandTarget}
        </button>
        <button
          type="button"
          className={`ge-icon-btn ${detailsOpen ? "active" : ""}`}
          onClick={onToggleDetails}
          title={detailsOpen ? "Hide details" : "Show details"}
          aria-pressed={detailsOpen}
        >
          ⓘ
        </button>
      </div>
    </header>
  );
}
