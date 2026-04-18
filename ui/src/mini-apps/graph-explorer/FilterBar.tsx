import type { GraphExplorerState, StatusFilter } from "./types";

interface Props {
  view: GraphExplorerState;
  onFocus: (patch: Partial<GraphExplorerState>) => void;
  onAskAssistant: () => void;
  onReset: () => void;
  detailsOpen: boolean;
  onToggleDetails: () => void;
  onExpand: () => void;
  isSampleMode: boolean;
}

/**
 * Single-row compact topbar. Design goals:
 *   - Every control carries its own visible affordance (no mystery numbers).
 *   - Two two-value toggles (layout, status) use segmented controls — clearer
 *     than dropdowns at tiny sizes, and no extra click to discover options.
 *   - Numeric inputs are self-labeled pills with inline units.
 *   - Right-side actions collapse to icons when cramped.
 */
export function FilterBar({
  view, onFocus, onAskAssistant, onReset, detailsOpen, onToggleDetails, onExpand, isSampleMode,
}: Props) {
  return (
    <header className="ge-topbar">
      <div className="ge-topbar-left">
        <span className="ge-title">Graph</span>
        {isSampleMode ? (
          <span className="ge-sample-tag" title="Sample preview — click any node to seed from it.">
            sample · {view.relation_types[0] ?? "profile"}
          </span>
        ) : null}
      </div>

      <div className="ge-topbar-filters">
        <label className="ge-pill" title="Time window (days)">
          <span className="ge-pill-icon" aria-hidden>🕑</span>
          <input
            type="number"
            min={1}
            max={3650}
            value={view.transfer_window_days}
            onChange={(e) =>
              onFocus({ transfer_window_days: Math.max(1, Number(e.target.value) || 0) })
            }
          />
          <span className="ge-pill-unit">d</span>
        </label>

        <label className="ge-pill" title="Max neighbors per hop">
          <span className="ge-pill-icon" aria-hidden>◎</span>
          <input
            type="number"
            min={1}
            max={500}
            value={view.max_neighbors}
            onChange={(e) =>
              onFocus({ max_neighbors: Math.max(1, Number(e.target.value) || 0) })
            }
          />
        </label>

        <div className="ge-segment" role="tablist" aria-label="Layout">
          <button
            type="button"
            className={view.layout === "force" ? "active" : ""}
            onClick={() => onFocus({ layout: "force" })}
            title="Force layout"
          >
            Force
          </button>
          <button
            type="button"
            className={view.layout === "circular" ? "active" : ""}
            onClick={() => onFocus({ layout: "circular" })}
            title="Circular layout"
          >
            Circle
          </button>
        </div>

        <div className="ge-segment ge-segment-status" role="tablist" aria-label="Semantic status">
          <button
            type="button"
            className={view.semantic_status_filter === "all" ? "active" : ""}
            onClick={() => onFocus({ semantic_status_filter: "all" as StatusFilter })}
            title="Show all profiles"
          >
            All
          </button>
          <button
            type="button"
            className={
              "appr " + (view.semantic_status_filter === "approved" ? "active" : "")
            }
            onClick={() => onFocus({ semantic_status_filter: "approved" as StatusFilter })}
            title="Approved only"
          >
            <span className="ge-dot ge-dot-approved" aria-hidden />
          </button>
          <button
            type="button"
            className={
              "cand " + (view.semantic_status_filter === "candidate" ? "active" : "")
            }
            onClick={() => onFocus({ semantic_status_filter: "candidate" as StatusFilter })}
            title="Candidate only"
          >
            <span className="ge-dot ge-dot-candidate" aria-hidden />
          </button>
        </div>
      </div>

      <div className="ge-topbar-right">
        <button
          type="button"
          className="ge-icon-btn"
          onClick={onExpand}
          title="Expand seed +1 hop"
          disabled={!view.seed_node?.id}
        >
          +
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
        <button
          type="button"
          className="ge-icon-btn"
          onClick={onReset}
          title="Back to catalog"
        >
          ↺
        </button>
        <button
          type="button"
          className="ge-btn primary"
          onClick={onAskAssistant}
          title="Ask the assistant about this subgraph"
        >
          Ask
        </button>
      </div>
    </header>
  );
}
