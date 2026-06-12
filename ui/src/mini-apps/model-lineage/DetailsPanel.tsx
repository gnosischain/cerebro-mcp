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
  const [showSchema, setShowSchema] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [sqlMode, setSqlMode] = useState<"raw" | "compiled">("raw");

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
  const isSource = selected.kind === "source";
  const cols = selected.columns ?? [];
  const sqlText = sqlMode === "raw" ? selected.rawSql : selected.compiledSql;
  const hasSql = Boolean(selected.rawSql || selected.compiledSql);

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
        {/* Sources are leaf inputs with no upstream lineage — no expand. */}
        {!isSource ? (
          <button type="button" onClick={() => onExpand(selected.id)}>
            Expand neighbours
          </button>
        ) : null}
        {isModel ? (
          <button type="button" onClick={() => onRecenter(selected.name)}>
            Recenter here
          </button>
        ) : null}
      </div>

      {cols.length ? (
        <div className="ml-details-section">
          <button
            type="button"
            className="ml-section-toggle"
            aria-expanded={showSchema}
            onClick={() => setShowSchema((v) => !v)}
          >
            <span className="ml-section-caret">{showSchema ? "▾" : "▸"}</span>
            Schema <span className="ml-section-count">{cols.length}</span>
          </button>
          {showSchema ? (
            <div className="ml-schema-table-wrap">
              <table className="ml-schema-table">
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Type</th>
                  </tr>
                </thead>
                <tbody>
                  {cols.map((c) => (
                    <tr key={c.name} title={c.description || undefined}>
                      <td className="ml-schema-col">{c.name}</td>
                      <td className="ml-schema-type">{c.data_type || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}

      {hasSql ? (
        <div className="ml-details-section">
          <button
            type="button"
            className="ml-section-toggle"
            aria-expanded={showSql}
            onClick={() => setShowSql((v) => !v)}
          >
            <span className="ml-section-caret">{showSql ? "▾" : "▸"}</span>
            SQL
          </button>
          {showSql ? (
            <div className="ml-sql-block">
              <div className="ml-segment ml-sql-modes" role="tablist">
                <button
                  type="button"
                  className={sqlMode === "raw" ? "active" : ""}
                  onClick={() => setSqlMode("raw")}
                  disabled={!selected.rawSql}
                >
                  Raw
                </button>
                <button
                  type="button"
                  className={sqlMode === "compiled" ? "active" : ""}
                  onClick={() => setSqlMode("compiled")}
                  disabled={!selected.compiledSql}
                >
                  Compiled
                </button>
              </div>
              <pre className="ml-sql-pre">
                <code>{sqlText || "— not available —"}</code>
              </pre>
            </div>
          ) : null}
        </div>
      ) : isSource ? (
        <p className="ml-details-note">No SQL — source table.</p>
      ) : null}

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
