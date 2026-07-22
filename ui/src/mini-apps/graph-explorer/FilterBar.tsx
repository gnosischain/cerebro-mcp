// Investigate-mode topbar: window / max-neighbors (debounced refetch by the
// parent), the Edge-types popover (slot), expand-depth stepper (reads the
// SERVER-published limits — no compile-time hop cap), and the explicit
// "+ Expand" button. Expansion happens ONLY on explicit action (this button
// or a canvas double-click) — never silently on stepper change (WS10-F).

import type { ReactNode } from "react";
import { FilterDrawer } from "./FilterDrawer";
import type { Limits } from "./types";

interface Props {
  windowDays: number;
  maxNeighbors: number;
  expandDepth: number;
  limits: Limits;
  /** "selected node" or "seed" — surfaced on the expand button. */
  expandTarget: string;
  canExpand: boolean;
  detailsOpen: boolean;
  /** Seed identity cell (label + address + copy) — owned by InvestigateView.
   * Lives IN the bar so the app has exactly one header row. */
  leftSlot?: ReactNode;
  /** The Edge-types popover button (owned by InvestigateView). */
  edgeTypesSlot?: ReactNode;
  /** Mode switch (Atlas | Investigate | …) — rendered at the far end. */
  endSlot?: ReactNode;
  /** Evidence/status trigger immediately beside the inspector control. */
  accessorySlot?: ReactNode;
  statusSlot?: ReactNode;
  onWindowChange: (days: number) => void;
  onMaxNeighborsChange: (value: number) => void;
  onExpandDepthChange: (depth: number) => void;
  onExpand: () => void;
  onToggleDetails: () => void;
}

export function FilterBar({
  windowDays,
  maxNeighbors,
  expandDepth,
  limits,
  expandTarget,
  canExpand,
  detailsOpen,
  leftSlot,
  edgeTypesSlot,
  endSlot,
  accessorySlot,
  statusSlot,
  onWindowChange,
  onMaxNeighborsChange,
  onExpandDepthChange,
  onExpand,
  onToggleDetails,
}: Props) {
  const maxHops = Math.max(1, limits.max_hops);
  return (
    <header className="ge-topbar">
      {leftSlot}
      <FilterDrawer>
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

        {/* Layout toggle removed — force is the only layout (tunable live
            via the canvas "⚙ Forces" panel). The semantic-status filter
            moved into the Edge types popover. */}
      </FilterDrawer>

      {/* Outside .ge-topbar-filters — its overflow-x scroll would clip the
          absolutely-positioned dropdown panel. */}
      {edgeTypesSlot}

      <div className="ge-topbar-right">
        {statusSlot}
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
        {accessorySlot}
        <button
          type="button"
          className={`ge-icon-btn ${detailsOpen ? "active" : ""}`}
          onClick={onToggleDetails}
          title={detailsOpen ? "Hide details" : "Show details"}
          aria-pressed={detailsOpen}
        >
          ⓘ
        </button>
        {endSlot}
      </div>
    </header>
  );
}
