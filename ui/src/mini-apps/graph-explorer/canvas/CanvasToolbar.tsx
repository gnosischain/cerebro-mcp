// Top-left in-canvas controls: search / fit / recenter / focus / play-pause /
// label mode. Presentational — all state lives in GraphCanvas.

import type { LabelMode } from "./LabelsOverlay";

interface Props {
  search: string;
  searchMiss: boolean;
  onSearchChange: (value: string) => void;
  onRunSearch: () => void;
  onFit: () => void;
  onRecenter: () => void;
  focusMode: boolean;
  onToggleFocus: () => void;
  simRunning: boolean;
  onToggleSim: () => void;
  labelMode: LabelMode;
  onLabelModeChange: (mode: LabelMode) => void;
}

export function CanvasToolbar({
  search,
  searchMiss,
  onSearchChange,
  onRunSearch,
  onFit,
  onRecenter,
  focusMode,
  onToggleFocus,
  simRunning,
  onToggleSim,
  labelMode,
  onLabelModeChange,
}: Props) {
  return (
    <div className="ge-graph-controls">
      <div className={`ge-graph-search ${searchMiss ? "miss" : ""}`}>
        <input
          type="text"
          placeholder={searchMiss ? "No match" : "Find node by address/label…"}
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onRunSearch();
          }}
        />
        <button type="button" onClick={onRunSearch} title="Find & zoom">
          ⌕
        </button>
      </div>
      <button
        type="button"
        className="ge-graph-btn"
        onClick={onFit}
        title="Zoom to fit"
      >
        Fit
      </button>
      <button
        type="button"
        className="ge-graph-btn"
        onClick={onRecenter}
        title="Recenter"
      >
        Recenter
      </button>
      <button
        type="button"
        className={`ge-graph-btn ${focusMode ? "active" : ""}`}
        onClick={onToggleFocus}
        title="Focus mode — isolate the selected node and its neighbors"
        aria-pressed={focusMode}
      >
        Focus
      </button>
      <button
        type="button"
        className={`ge-graph-btn ${simRunning ? "active" : ""}`}
        onClick={onToggleSim}
        title={
          simRunning
            ? "Pause the layout simulation"
            : "Play — re-energize the layout and watch it evolve"
        }
        aria-pressed={simRunning}
      >
        {simRunning ? "❚❚ Pause" : "▶ Play"}
      </button>
      <div className="ge-label-mode" role="group" aria-label="Label visibility">
        <span className="ge-label-mode-caption">Labels</span>
        {(["all", "auto", "off"] as const).map((m) => (
          <button
            key={m}
            type="button"
            className={`ge-graph-btn ${labelMode === m ? "active" : ""}`}
            onClick={() => onLabelModeChange(m)}
            aria-pressed={labelMode === m}
          >
            {m === "all" ? "All" : m === "auto" ? "Auto" : "Off"}
          </button>
        ))}
      </div>
    </div>
  );
}
