import { useState } from "react";
import { parseNodeRow, type ModelNodeData } from "./types";
import type { DatasetDescriptor } from "../shared/miniAppTypes";

interface DetailsPanelProps {
  nodes?: DatasetDescriptor;
  selectedNodeId: string;
  collapsed: boolean;
  onToggleCollapse: () => void;
  onExpand: (id: string) => void;
  onRecenter: (modelName: string) => void;
  onTraceColumn: (modelName: string, column: string) => void;
}

export function DetailsPanel({
  nodes,
  selectedNodeId,
  collapsed,
  onToggleCollapse,
  onExpand,
  onRecenter,
  onTraceColumn,
}: DetailsPanelProps) {
  const [column, setColumn] = useState("");

  // Collapsed → render a thin rail with a reopen toggle, freeing the canvas.
  if (collapsed) {
    return (
      <aside className="ml-details is-collapsed">
        <button
          type="button"
          className="ml-details-toggle"
          onClick={onToggleCollapse}
          title="Show details"
          aria-label="Show details panel"
        >
          ‹
        </button>
      </aside>
    );
  }

  const selected: ModelNodeData | undefined = (nodes?.preview_rows ?? [])
    .map(parseNodeRow)
    .find((n) => n.id === selectedNodeId);

  const collapseBtn = (
    <button
      type="button"
      className="ml-details-toggle ml-details-toggle--open"
      onClick={onToggleCollapse}
      title="Hide details"
      aria-label="Hide details panel"
    >
      ›
    </button>
  );

  if (!selected) {
    return (
      <aside className="ml-details">
        {collapseBtn}
        <div className="ml-details-empty">
          Select a model to see its details and trace column lineage.
        </div>
      </aside>
    );
  }

  const isModel = selected.kind === "model";

  return (
    <aside className="ml-details">
      {collapseBtn}
      <h3 className="ml-details-name">{selected.name}</h3>
      <dl className="ml-details-meta">
        <div>
          <dt>Kind</dt>
          <dd>{selected.kind}</dd>
        </div>
        {selected.materialized ? (
          <div>
            <dt>Materialized</dt>
            <dd>{selected.materialized}</dd>
          </div>
        ) : null}
        {selected.schema ? (
          <div>
            <dt>Schema</dt>
            <dd>{selected.schema}</dd>
          </div>
        ) : null}
        <div>
          <dt>Columns</dt>
          <dd>{selected.columnCount}</dd>
        </div>
        {selected.testCount > 0 ? (
          <div>
            <dt>Tests</dt>
            <dd>{selected.testCount}</dd>
          </div>
        ) : null}
      </dl>

      {selected.description ? (
        <p className="ml-details-desc">{selected.description}</p>
      ) : null}

      {selected.tags?.length ? (
        <div className="ml-details-tags">
          {selected.tags.map((t) => (
            <span key={t} className="ml-tag">
              {t}
            </span>
          ))}
        </div>
      ) : null}

      <div className="ml-details-actions">
        <button type="button" onClick={() => onExpand(selected.id)}>
          Expand neighbours
        </button>
        {isModel ? (
          <button type="button" onClick={() => onRecenter(selected.name)}>
            Recenter here
          </button>
        ) : null}
      </div>

      {isModel ? (
        <div className="ml-column-trace">
          <label htmlFor="ml-column-input">Trace column lineage</label>
          <div className="ml-column-trace-row">
            <input
              id="ml-column-input"
              type="text"
              value={column}
              placeholder="column name"
              onChange={(e) => setColumn(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && column.trim()) {
                  onTraceColumn(selected.name, column.trim());
                }
              }}
            />
            <button
              type="button"
              disabled={!column.trim()}
              onClick={() => onTraceColumn(selected.name, column.trim())}
            >
              Trace
            </button>
          </div>
        </div>
      ) : null}
    </aside>
  );
}
