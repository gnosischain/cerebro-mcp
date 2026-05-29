import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { ModelNodeData } from "./types";

const MATERIALIZED_TONE: Record<string, string> = {
  table: "mat-table",
  incremental: "mat-incremental",
  view: "mat-view",
  ephemeral: "mat-ephemeral",
};

/**
 * Custom React Flow node rendering a dbt model as a card: name,
 * materialization badge, schema, tags, and column/test counts. Source/target
 * handles drive click-to-expand (upstream on the left, downstream on the right).
 */
function ModelNodeImpl({ data, selected }: NodeProps) {
  const node = data as ModelNodeData;
  const matClass = MATERIALIZED_TONE[node.materialized] ?? "mat-other";
  const isSeed = Boolean(node.isSeed);
  const isSource = node.kind === "source";

  return (
    <div
      className={`ml-node ${selected ? "is-selected" : ""} ${isSeed ? "is-seed" : ""} ${
        isSource ? "is-source" : ""
      }`}
      title={node.description || node.name}
    >
      <Handle type="target" position={Position.Left} className="ml-handle" />
      <div className="ml-node-header">
        <span className="ml-node-name">{node.name}</span>
        {node.materialized ? (
          <span className={`ml-mat-badge ${matClass}`}>{node.materialized}</span>
        ) : isSource ? (
          <span className="ml-mat-badge mat-source">source</span>
        ) : null}
      </div>
      {node.schema ? <div className="ml-node-schema">{node.schema}</div> : null}
      {node.tags?.length ? (
        <div className="ml-node-tags">
          {node.tags.slice(0, 4).map((t) => (
            <span key={t} className="ml-tag">
              {t}
            </span>
          ))}
          {node.tags.length > 4 ? (
            <span className="ml-tag ml-tag-more">+{node.tags.length - 4}</span>
          ) : null}
        </div>
      ) : null}
      <div className="ml-node-meta">
        <span title="columns">{node.columnCount} cols</span>
        {node.testCount > 0 ? <span title="tests">{node.testCount} tests</span> : null}
      </div>
      <Handle type="source" position={Position.Right} className="ml-handle" />
    </div>
  );
}

export const ModelNode = memo(ModelNodeImpl);
