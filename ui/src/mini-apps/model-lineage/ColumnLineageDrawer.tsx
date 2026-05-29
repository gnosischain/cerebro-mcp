import { parseColumnEdgeRow, type ColumnEdgeRow } from "./types";
import type { DatasetDescriptor } from "../shared/miniAppTypes";

interface ColumnLineageDrawerProps {
  columnEdges?: DatasetDescriptor;
  selectedColumn: string;
  modelName: string;
  open: boolean;
  onClose: () => void;
}

/**
 * Slide-in drawer that lists the column-to-column lineage edges returned by
 * the `load_column_lineage` tool. Each edge is rendered as a
 * `source_model.source_column → target_model.target_column` row. When the
 * backend degraded to a model-level fallback (level === "model"), the columns
 * are null and we render a model→model edge with a "model-level" tag.
 */
export function ColumnLineageDrawer({
  columnEdges,
  selectedColumn,
  modelName,
  open,
  onClose,
}: ColumnLineageDrawerProps) {
  if (!open) return null;

  const edges: ColumnEdgeRow[] = (columnEdges?.preview_rows ?? []).map(
    parseColumnEdgeRow,
  );

  const isModelLevel = edges.length > 0 && edges.every((e) => e.level === "model");

  const fmt = (model: string, column: string | null) =>
    column ? `${model}.${column}` : model;

  return (
    <aside className={`ml-col-drawer ${open ? "is-open" : ""}`}>
      <header className="ml-col-drawer-head">
        <div>
          <h3 className="ml-col-drawer-title">Column lineage</h3>
          <div className="ml-col-drawer-sub">
            {modelName ? `${modelName}.${selectedColumn}` : selectedColumn}
          </div>
        </div>
        <button
          type="button"
          className="ml-col-drawer-close"
          onClick={onClose}
          aria-label="Close column lineage"
        >
          ×
        </button>
      </header>

      {isModelLevel ? (
        <div className="ml-col-drawer-note">
          Column-level parse unavailable for this model — showing model-level
          lineage instead.
        </div>
      ) : null}

      {edges.length === 0 ? (
        <div className="ml-col-drawer-empty">
          No column lineage edges. Trace a column from the details panel to
          populate this drawer.
        </div>
      ) : (
        <ol className="ml-col-edge-list">
          {edges.map((e) => (
            <li key={e.id} className="ml-col-edge">
              <span className="ml-col-edge-src">
                {fmt(e.sourceModel, e.sourceColumn)}
              </span>
              <span className="ml-col-edge-arrow" aria-hidden>
                →
              </span>
              <span className="ml-col-edge-tgt">
                {fmt(e.targetModel, e.targetColumn)}
              </span>
              {e.level === "model" ? (
                <span className="ml-tag ml-tag-more">model-level</span>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}
