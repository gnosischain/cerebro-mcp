import ReactECharts from "echarts-for-react";
import { useMemo } from "react";
import type { DatasetDescriptor } from "../shared/miniAppTypes";
import type { GraphEdgeRow, GraphNodeRow } from "./types";

interface Props {
  nodes?: DatasetDescriptor;
  edges?: DatasetDescriptor;
  selectedNodeId: string;
  selectedEdgeId: string;
  activeProfiles: string[];
  layout: "force" | "circular";
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onExpandNode: (id: string) => void;
}

const COLOR_BY_KIND: Record<string, string> = {
  address: "#6ee7b7",
  safe: "#a78bfa",
  gpay_wallet: "#fbbf24",
  circles_avatar: "#60a5fa",
  circles_wrapper: "#38bdf8",
  token: "#f472b6",
  pool: "#c084fc",
  validator: "#f97316",
  bridge: "#facc15",
  project_label: "#94a3b8",
};

function parseNodeRow(row: unknown[]): GraphNodeRow {
  const [id, kind, label, profiles] = row as [string, string, string, string[] | null];
  return {
    id: String(id ?? ""),
    kind: String(kind ?? "address"),
    label: String(label ?? ""),
    profiles: Array.isArray(profiles) ? (profiles as string[]) : [],
  };
}

function parseEdgeRow(row: unknown[]): GraphEdgeRow {
  const [id, source, target, profile, weight, edge_count, directed] = row as [
    string, string, string, string, number, number, boolean
  ];
  return {
    id: String(id ?? ""),
    source: String(source ?? ""),
    target: String(target ?? ""),
    profile: String(profile ?? ""),
    weight: Number(weight ?? 0),
    edge_count: Number(edge_count ?? 0),
    directed: Boolean(directed),
  };
}

export function ForceGraph({
  nodes,
  edges,
  selectedNodeId,
  selectedEdgeId,
  activeProfiles,
  layout,
  onSelectNode,
  onSelectEdge,
  onExpandNode,
}: Props) {
  const { option, hasData } = useMemo(() => {
    const nodeRows = (nodes?.preview_rows ?? []).map(parseNodeRow);
    const edgeRows = (edges?.preview_rows ?? []).map(parseEdgeRow);
    const activeSet = new Set(activeProfiles);

    const presentKinds = Array.from(new Set(nodeRows.map((n) => n.kind)));
    const categories = presentKinds.map((kind) => ({
      name: kind,
      itemStyle: { color: COLOR_BY_KIND[kind] ?? "#9ca3af" },
    }));

    const graphNodes = nodeRows.map((n) => {
      const isSelected = n.id === selectedNodeId;
      return {
        id: n.id,
        name: n.label || n.id,
        // Selected nodes are the same size as everything else; selection
        // is indicated ONLY by a thin white border. No shadow, no upsizing
        // — the user was seeing the shadow as a "giant blue circle".
        symbolSize: 14,
        category: presentKinds.indexOf(n.kind),
        itemStyle: {
          color: COLOR_BY_KIND[n.kind] ?? "#9ca3af",
          borderColor: isSelected ? "#f8fafc" : "transparent",
          borderWidth: isSelected ? 2 : 0,
        },
        label: { show: nodeRows.length <= 60 },
        _profiles: n.profiles,
        _kind: n.kind,
      };
    });

    const graphLinks = edgeRows
      .filter((e) => !activeSet.size || activeSet.has(e.profile))
      .map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        value: e.weight,
        lineStyle: {
          width: Math.max(1, Math.log10((e.weight || 1) + 1) * 1.2),
          opacity: e.id === selectedEdgeId ? 1 : 0.55,
          curveness: 0.1,
        },
        _profile: e.profile,
      }));

    return {
      hasData: nodeRows.length > 0,
      option: {
        tooltip: {
          formatter: (x: { dataType: string; data: Record<string, unknown> }) => {
            if (x.dataType === "edge") {
              const d = x.data as { source: string; target: string; _profile: string; value: number };
              return `${d.source}<br/>→ ${d.target}<br/>${d._profile} · w=${d.value.toFixed(2)}`;
            }
            const d = x.data as { name: string; _profiles: string[]; _kind: string };
            return `${d.name}<br/><span style="color:#94a3b8">${d._kind}</span><br/>${(d._profiles || []).join(", ")}`;
          },
        },
        legend: [{ data: presentKinds, textStyle: { color: "#94a3b8" } }],
        series: [
          {
            type: "graph",
            layout,
            roam: true,
            draggable: true,
            animation: true,
            label: { position: "right", color: "#cbd5e1" },
            force: { repulsion: 220, edgeLength: [60, 150], gravity: 0.04 },
            emphasis: { focus: "adjacency", lineStyle: { width: 3 } },
            data: graphNodes,
            links: graphLinks,
            categories,
            edgeSymbol: ["none", "arrow"],
            edgeSymbolSize: 8,
          },
        ],
      },
    };
  }, [nodes, edges, selectedNodeId, selectedEdgeId, activeProfiles, layout]);

  if (!hasData) {
    return (
      <div className="ge-placeholder">
        <span>No nodes yet — seed an address from the catalog.</span>
      </div>
    );
  }

  return (
    <ReactECharts
      option={option}
      style={{ height: "100%", width: "100%" }}
      onEvents={{
        click: (params: { dataType?: string; data?: { id?: string } }) => {
          if (params.dataType === "node" && params.data?.id) {
            onSelectNode(params.data.id);
          } else if (params.dataType === "edge" && params.data?.id) {
            onSelectEdge(params.data.id);
          }
        },
        dblclick: (params: { dataType?: string; data?: { id?: string } }) => {
          if (params.dataType === "node" && params.data?.id) {
            onExpandNode(params.data.id);
          }
        },
      }}
    />
  );
}
