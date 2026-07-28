// Top-left in-canvas controls: search / fit / recenter / focus / play-pause /
// sim-parameter panel / label mode. Presentational — all state lives in
// GraphCanvas.

import { useState } from "react";
import type { LabelMode } from "./LabelsOverlay";

/** Live-tunable force-simulation parameters (pushed via graph.setConfig). */
export interface SimParams {
  repulsion: number;
  linkDistance: number;
  gravity: number;
  friction: number;
}

const SLIDERS: Array<{
  key: keyof SimParams;
  label: string;
  min: number;
  max: number;
  step: number;
  hint: string;
}> = [
  { key: "repulsion", label: "Repulsion", min: 0.2, max: 6, step: 0.1,
    hint: "How strongly nodes push apart — raise to spread dense clusters" },
  { key: "linkDistance", label: "Link distance", min: 10, max: 300, step: 5,
    hint: "Resting edge length" },
  { key: "gravity", label: "Gravity", min: 0, max: 0.6, step: 0.01,
    hint: "Pull toward the center — raise to keep the cloud compact" },
  { key: "friction", label: "Friction", min: 0.6, max: 1, step: 0.01,
    hint: "Damping — lower settles faster but jitters more" },
];

interface Props {
  search: string;
  searchMiss: boolean;
  onSearchChange: (value: string) => void;
  onRunSearch: () => void;
  onFitView: () => void;
  focusMode: boolean;
  onToggleFocus: () => void;
  simRunning: boolean;
  onToggleSim: () => void;
  /** Optional custom labels for the sim button (Timeline uses "Layout" to
   * distinguish from its scrubber "Time" play). Defaults to Play/Pause. */
  simControlLabel?: { play: string; pause: string };
  /** Hide Play/Pause (static-layout modes have no running sim). */
  showSimControls?: boolean;
  /** Hide the ⚙ Forces panel (static layouts have no sim to tune). */
  showForcesPanel?: boolean;
  simParams: SimParams;
  onSimParamsChange: (next: SimParams) => void;
  labelMode: LabelMode;
  onLabelModeChange: (mode: LabelMode) => void;
  /** Empty graph: the toolbar stays MOUNTED but its actions are inert. The
   * user keeps the shape of the workspace (and the Advanced panel) instead of
   * watching every control disappear along with the data. */
  disabled?: boolean;
}

export function CanvasToolbar({
  search,
  searchMiss,
  onSearchChange,
  onRunSearch,
  onFitView,
  focusMode,
  onToggleFocus,
  simRunning,
  onToggleSim,
  simControlLabel,
  showSimControls = true,
  showForcesPanel = true,
  simParams,
  onSimParamsChange,
  labelMode,
  onLabelModeChange,
  disabled = false,
}: Props) {
  const [simPanelOpen, setSimPanelOpen] = useState(false);
  return (
    <div className={`ge-graph-controls${disabled ? " is-disabled" : ""}`}>
      <div className={`ge-graph-search ${searchMiss ? "miss" : ""}`}>
        <input
          type="text"
          placeholder={searchMiss ? "No match" : "Find node by address/label…"}
          value={search}
          disabled={disabled}
          onChange={(e) => onSearchChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onRunSearch();
          }}
        />
        <button
          type="button"
          onClick={onRunSearch}
          disabled={disabled}
          title="Find & zoom"
        >
          ⌕
        </button>
      </div>
      <button
        type="button"
        className="ge-graph-btn"
        onClick={onFitView}
        disabled={disabled}
        title="Fit the complete graph in the viewport"
      >
        Fit view
      </button>
      <button
        type="button"
        className={`ge-graph-btn ${focusMode ? "active" : ""}`}
        onClick={onToggleFocus}
        disabled={disabled}
        title="Focus mode — isolate the selected node and its neighbors"
        aria-pressed={focusMode}
      >
        Focus
      </button>
      <details className="ge-canvas-advanced">
        <summary className="ge-graph-btn">Advanced</summary>
        <div className="ge-canvas-advanced__panel">
      {showSimControls ? (
        <button
          type="button"
          className={`ge-graph-btn ${simRunning ? "active" : ""}`}
          onClick={onToggleSim}
          title={
            simRunning
              ? "Pause the layout simulation (freeze positions)"
              : "Play — run the force layout"
          }
          aria-pressed={simRunning}
        >
          {simRunning
            ? simControlLabel?.pause ?? "❚❚ Pause"
            : simControlLabel?.play ?? "▶ Play"}
        </button>
      ) : null}
      {showForcesPanel ? (
      <div className="ge-sim-tune">
        <button
          type="button"
          className={`ge-graph-btn ${simPanelOpen ? "active" : ""}`}
          onClick={() => setSimPanelOpen((v) => !v)}
          title="Tune the force-layout parameters live"
          aria-pressed={simPanelOpen}
          aria-expanded={simPanelOpen}
        >
          ⚙ Forces
        </button>
        {simPanelOpen ? (
          <div className="ge-sim-panel" role="group" aria-label="Simulation parameters">
            {SLIDERS.map((s) => (
              <label key={s.key} className="ge-sim-row" title={s.hint}>
                <span className="ge-sim-label">{s.label}</span>
                <input
                  type="range"
                  min={s.min}
                  max={s.max}
                  step={s.step}
                  value={simParams[s.key]}
                  onChange={(e) =>
                    onSimParamsChange({
                      ...simParams,
                      [s.key]: Number(e.target.value),
                    })
                  }
                />
                <span className="ge-sim-value">{simParams[s.key]}</span>
              </label>
            ))}
          </div>
        ) : null}
      </div>
      ) : null}
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
      </details>
    </div>
  );
}
