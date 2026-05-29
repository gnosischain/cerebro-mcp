import { useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
  type NodeTypes,
  MarkerType,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";
import { ModelNode } from "./ModelNode";
import {
  parseEdgeRow,
  parseNodeRow,
  type ModelNodeData,
} from "./types";
import type { DatasetDescriptor } from "../shared/miniAppTypes";

const NODE_WIDTH = 220;
const NODE_HEIGHT = 96;

const nodeTypes: NodeTypes = { model: ModelNode };

interface LineageGraphProps {
  nodes?: DatasetDescriptor;
  edges?: DatasetDescriptor;
  seedId: string;
  selectedNodeId: string;
  onSelectNode: (id: string) => void;
  onExpandNode: (id: string) => void;
}

/** Compute a left-to-right dagre layout for the lineage DAG. */
function layout(
  rfNodes: Node[],
  rfEdges: Edge[],
): Node[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 24, ranksep: 80, marginx: 24, marginy: 24 });

  for (const n of rfNodes) {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const e of rfEdges) {
    g.setEdge(e.source, e.target);
  }
  dagre.layout(g);

  return rfNodes.map((n) => {
    const pos = g.node(n.id);
    return {
      ...n,
      position: pos
        ? { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 }
        : { x: 0, y: 0 },
    };
  });
}

export function LineageGraph({
  nodes,
  edges,
  seedId,
  selectedNodeId,
  onSelectNode,
  onExpandNode,
}: LineageGraphProps) {
  const { rfNodes, rfEdges } = useMemo(() => {
    const nodeRows = (nodes?.preview_rows ?? []).map(parseNodeRow);
    const edgeRows = (edges?.preview_rows ?? []).map(parseEdgeRow);

    // Highlight the connected neighbourhood of the selected node.
    const highlightId = selectedNodeId || seedId;
    const connected = new Set<string>();
    if (highlightId) {
      connected.add(highlightId);
      for (const e of edgeRows) {
        if (e.source === highlightId) connected.add(e.target);
        if (e.target === highlightId) connected.add(e.source);
      }
    }

    const builtNodes: Node[] = nodeRows.map((row: ModelNodeData) => {
      const isHighlighted = !highlightId || connected.has(row.id);
      return {
        id: row.id,
        type: "model",
        position: { x: 0, y: 0 },
        data: { ...row, isSeed: row.id === seedId },
        selected: row.id === selectedNodeId,
        style: { opacity: isHighlighted ? 1 : 0.25 },
      };
    });

    const builtEdges: Edge[] = edgeRows.map((e) => {
      const onPath =
        !highlightId || e.source === highlightId || e.target === highlightId;
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        animated: onPath && Boolean(highlightId),
        markerEnd: { type: MarkerType.ArrowClosed },
        style: {
          stroke: onPath ? "var(--ml-edge-active, #6366f1)" : "var(--ml-edge, #475569)",
          strokeWidth: onPath ? 2 : 1,
          opacity: onPath ? 1 : 0.35,
        },
      };
    });

    return { rfNodes: layout(builtNodes, builtEdges), rfEdges: builtEdges };
  }, [nodes, edges, seedId, selectedNodeId]);

  if (rfNodes.length === 0) {
    return (
      <div className="ml-canvas-empty">
        No lineage loaded. Provide a seed model to begin.
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={rfNodes}
      edges={rfEdges}
      nodeTypes={nodeTypes}
      fitView
      minZoom={0.1}
      maxZoom={1.75}
      proOptions={{ hideAttribution: true }}
      onNodeClick={(_, node) => onSelectNode(node.id)}
      onNodeDoubleClick={(_, node) => onExpandNode(node.id)}
    >
      <Background gap={20} />
      <Controls showInteractive={false} />
      <MiniMap
        className="ml-minimap"
        pannable
        zoomable
        nodeStrokeWidth={2}
        bgColor="var(--surface, #131c2e)"
        maskColor="rgba(15, 23, 42, 0.55)"
        nodeColor="var(--accent, #6366f1)"
        nodeStrokeColor="var(--border, #334155)"
      />
    </ReactFlow>
  );
}
