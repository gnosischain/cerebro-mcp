import { useState } from "react";
import type { LineageDirection, LineageLayer, ModelLineageState } from "./types";

interface FilterBarProps {
  state: ModelLineageState;
  onSeed: (model: string) => void;
  onLayerChange: (layer: LineageLayer) => void;
  onDirectionChange: (direction: LineageDirection) => void;
  onDepthChange: (depth: number) => void;
  onBrowse: () => void;
  nodeCount: number;
  edgeCount: number;
}

const DIRECTIONS: LineageDirection[] = ["upstream", "both", "downstream"];

export function FilterBar({
  state,
  onSeed,
  onLayerChange,
  onDirectionChange,
  onDepthChange,
  onBrowse,
  nodeCount,
  edgeCount,
}: FilterBarProps) {
  const [seedInput, setSeedInput] = useState(state.seed);

  const submitSeed = () => {
    const trimmed = seedInput.trim();
    if (trimmed) onSeed(trimmed);
  };

  const semantic = state.layer === "semantic";

  return (
    <div className="ml-filterbar">
      <div className="ml-seed-input">
        <input
          type="text"
          value={seedInput}
          placeholder="Seed model (e.g. fct_execution_pools_daily)"
          onChange={(e) => setSeedInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submitSeed();
          }}
        />
        <button type="button" onClick={submitSeed}>
          Load
        </button>
        <button type="button" className="ml-browse-btn" onClick={onBrowse}>
          Browse
        </button>
      </div>

      <div className="ml-layer-toggle" role="tablist" aria-label="Lineage layer">
        <button
          type="button"
          role="tab"
          aria-selected={!semantic}
          className={!semantic ? "active" : ""}
          onClick={() => onLayerChange("model")}
        >
          Model DAG
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={semantic}
          className={semantic ? "active" : ""}
          onClick={() => onLayerChange("semantic")}
        >
          Semantic
        </button>
      </div>

      {!semantic && (
        <div className="ml-direction-group">
          {DIRECTIONS.map((d) => (
            <button
              key={d}
              type="button"
              className={state.direction === d ? "active" : ""}
              onClick={() => onDirectionChange(d)}
            >
              {d}
            </button>
          ))}
        </div>
      )}

      <label className="ml-depth-control">
        Depth
        <input
          type="range"
          min={1}
          max={5}
          value={state.depth}
          onChange={(e) => onDepthChange(Number(e.target.value))}
        />
        <span>{state.depth}</span>
      </label>

      <div className="ml-counts">
        <span>{nodeCount} nodes</span>
        <span>{edgeCount} edges</span>
      </div>
    </div>
  );
}
