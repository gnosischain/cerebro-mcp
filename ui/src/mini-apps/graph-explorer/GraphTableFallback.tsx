import type { CSSProperties } from "react";

export interface GraphTableFallbackNode {
  id: string;
  label?: string;
  kind?: string;
  summary?: string;
}

export interface GraphTableFallbackEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  weight?: number | string | null;
  directed?: boolean;
  summary?: string;
}

export interface GraphTableFallbackModel {
  nodes: GraphTableFallbackNode[];
  edges: GraphTableFallbackEdge[];
}

export interface GraphTableFallbackProps {
  model: GraphTableFallbackModel;
  error?: Error | null;
  title?: string;
  emptyMessage?: string;
  selectedNodeId?: string | null;
  selectedEdgeId?: string | null;
  onRetry?: () => void;
  onSelectNode?: (nodeId: string) => void;
  onSelectEdge?: (edgeId: string) => void;
  onNodeAction?: (nodeId: string) => void;
  nodeActionLabel?: string;
  onEdgeAction?: (edgeId: string) => void;
  edgeActionLabel?: string;
  className?: string;
  style?: CSSProperties;
}

const panelStyle: CSSProperties = {
  minWidth: 0,
  maxHeight: "100%",
  overflow: "auto",
  padding: 16,
  background: "#f8fafc",
  color: "#0f172a",
  border: "1px solid #cbd5e1",
  borderRadius: 8,
};

const tableStyle: CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 13,
};

const cellStyle: CSSProperties = {
  borderBottom: "1px solid #e2e8f0",
  padding: "8px 10px",
  textAlign: "left",
  verticalAlign: "top",
  overflowWrap: "anywhere",
};

const actionStyle: CSSProperties = {
  border: 0,
  padding: 0,
  background: "transparent",
  color: "#1d4ed8",
  cursor: "pointer",
  font: "inherit",
  textAlign: "left",
  textDecoration: "underline",
  textUnderlineOffset: 2,
};

function idAction(
  id: string,
  label: string,
  selected: boolean,
  onSelect: ((id: string) => void) | undefined,
) {
  if (!onSelect) return <span title={id}>{label}</span>;
  return (
    <button
      type="button"
      style={actionStyle}
      title={id}
      aria-label={`Select ${id}`}
      aria-pressed={selected}
      onClick={() => onSelect(id)}
    >
      {label}
    </button>
  );
}

/**
 * Keyboard-native graph representation used for WebGL absence and renderer
 * crashes. It is a first-class selection surface, not a screenshot or error
 * placeholder: every node and edge remains reachable through real buttons.
 */
export function GraphTableFallback({
  model,
  error,
  title = "Graph table",
  emptyMessage = "No graph rows are available for this scope.",
  selectedNodeId,
  selectedEdgeId,
  onRetry,
  onSelectNode,
  onSelectEdge,
  onNodeAction,
  nodeActionLabel = "Investigate from here",
  onEdgeAction,
  edgeActionLabel = "Open transactions",
  className,
  style,
}: GraphTableFallbackProps) {
  const empty = model.nodes.length === 0 && model.edges.length === 0;
  return (
    <section
      className={className}
      style={{ ...panelStyle, ...style }}
      aria-label={title}
      data-graph-table-fallback="true"
    >
      <header style={{ marginBottom: 14 }}>
        <h2 style={{ margin: "0 0 6px", fontSize: 18 }}>{title}</h2>
        {error && (
          <div role="alert" style={{ color: "#991b1b" }}>
            <strong>Visual renderer unavailable.</strong>{" "}
            <span>{error.message}</span>
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                style={{ marginLeft: 10 }}
              >
                Retry visual renderer
              </button>
            )}
          </div>
        )}
      </header>

      {empty ? (
        <p>{emptyMessage}</p>
      ) : (
        <>
          <div style={{ overflowX: "auto", marginBottom: 18 }}>
            <table style={tableStyle}>
              <caption style={{ textAlign: "left", fontWeight: 700, paddingBottom: 6 }}>
                Nodes ({model.nodes.length})
              </caption>
              <thead>
                <tr>
                  <th scope="col" style={cellStyle}>Node</th>
                  <th scope="col" style={cellStyle}>Kind</th>
                  <th scope="col" style={cellStyle}>Summary</th>
                  {onNodeAction ? <th scope="col" style={cellStyle}>Action</th> : null}
                </tr>
              </thead>
              <tbody>
                {model.nodes.map((node) => {
                  const selected = node.id === selectedNodeId;
                  return (
                    <tr
                      key={node.id}
                      data-node-id={node.id}
                      aria-selected={selected}
                      style={{ background: selected ? "#dbeafe" : undefined }}
                    >
                      <td style={cellStyle}>
                        {idAction(node.id, node.label || node.id, selected, onSelectNode)}
                      </td>
                      <td style={cellStyle}>{node.kind || "unknown"}</td>
                      <td style={cellStyle}>{node.summary || "—"}</td>
                      {onNodeAction ? (
                        <td style={cellStyle}>
                          <button
                            type="button"
                            onClick={() => onNodeAction(node.id)}
                            aria-label={`${nodeActionLabel}: ${node.id}`}
                          >
                            {nodeActionLabel}
                          </button>
                        </td>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table style={tableStyle}>
              <caption style={{ textAlign: "left", fontWeight: 700, paddingBottom: 6 }}>
                Edges ({model.edges.length})
              </caption>
              <thead>
                <tr>
                  <th scope="col" style={cellStyle}>Edge</th>
                  <th scope="col" style={cellStyle}>Source</th>
                  <th scope="col" style={cellStyle}>Target</th>
                  <th scope="col" style={cellStyle}>Relationship</th>
                  <th scope="col" style={cellStyle}>Weight</th>
                  {onEdgeAction ? <th scope="col" style={cellStyle}>Action</th> : null}
                </tr>
              </thead>
              <tbody>
                {model.edges.map((edge) => {
                  const selected = edge.id === selectedEdgeId;
                  return (
                    <tr
                      key={edge.id}
                      data-edge-id={edge.id}
                      aria-selected={selected}
                      style={{ background: selected ? "#dbeafe" : undefined }}
                    >
                      <td style={cellStyle}>
                        {idAction(edge.id, edge.id, selected, onSelectEdge)}
                      </td>
                      <td style={cellStyle}>
                        {idAction(
                          edge.source,
                          edge.source,
                          edge.source === selectedNodeId,
                          onSelectNode,
                        )}
                      </td>
                      <td style={cellStyle}>
                        {idAction(
                          edge.target,
                          edge.target,
                          edge.target === selectedNodeId,
                          onSelectNode,
                        )}
                      </td>
                      <td style={cellStyle}>
                        {edge.label || (edge.directed === false ? "↔" : "→")}
                        {edge.summary ? ` · ${edge.summary}` : ""}
                      </td>
                      <td style={cellStyle}>
                        {Number.isFinite(edge.weight) ? String(edge.weight) : "unknown"}
                      </td>
                      {onEdgeAction ? (
                        <td style={cellStyle}>
                          <button
                            type="button"
                            onClick={() => onEdgeAction(edge.id)}
                            aria-label={`${edgeActionLabel}: ${edge.id}`}
                          >
                            {edgeActionLabel}
                          </button>
                        </td>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

export default GraphTableFallback;
