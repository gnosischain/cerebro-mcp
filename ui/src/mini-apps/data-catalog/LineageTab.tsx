import { useCallback, useEffect, useMemo, useState } from "react";
import { LineageGraph } from "../model-lineage/LineageGraph";
import { SegmentedControl } from "../shared/SegmentedControl";
import { WarningBanner } from "../shared/WarningBanner";
import type { DatasetDescriptor } from "../shared/miniAppTypes";
import type { EntityType, LineageNode, LineageResult } from "./types";

interface Props {
  entityName: string;
  callTool: <T = unknown>(
    name: string,
    args: Record<string, unknown>,
  ) => Promise<T | null>;
  onOpenEntity: (name: string, type: EntityType) => void;
}

// Build a minimal DatasetDescriptor whose `preview_rows` match the column order
// LineageGraph's parseNodeRow / parseEdgeRow expect. LineageGraph reads only
// `preview_rows`, so the rest is left empty (cast through unknown).
function nodeRows(nodes: LineageNode[]): unknown[][] {
  return nodes.map((n) => [
    n.id,
    n.name,
    n.kind,
    n.materialized ?? "",
    n.schema ?? "",
    n.tags ?? [],
    n.description ?? "",
    n.column_count ?? (n.columns?.length ?? 0),
    n.test_count ?? 0,
    n.columns ?? [],
    n.raw_sql ?? "",
    n.compiled_sql ?? "",
  ]);
}

function edgeRows(edges: LineageResult["edges"]): unknown[][] {
  return edges.map((e) => [e.id, e.source, e.target, "model"]);
}

function descriptor(rows: unknown[][]): DatasetDescriptor {
  return { preview_rows: rows } as unknown as DatasetDescriptor;
}

const DIRECTION_OPTS = [
  { value: "upstream", label: "Upstream" },
  { value: "both", label: "Both" },
  { value: "downstream", label: "Downstream" },
] as const;

const DEPTH_OPTS = [
  { value: "1", label: "1" },
  { value: "2", label: "2" },
  { value: "3", label: "3" },
] as const;

export function LineageTab({ entityName, callTool, onOpenEntity }: Props) {
  const [direction, setDirection] = useState<string>("both");
  const [depth, setDepth] = useState<string>("1");
  const [result, setResult] = useState<LineageResult | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await callTool<LineageResult>("catalog_lineage", {
        seed: entityName,
        direction,
        depth: Number(depth),
      });
      setResult(res);
      setSelectedNodeId(res?.seed_id ?? "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load lineage");
    } finally {
      setLoading(false);
    }
  }, [callTool, entityName, direction, depth]);

  useEffect(() => {
    void load();
  }, [load]);

  const nameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of result?.nodes ?? []) m.set(n.id, n.name);
    return m;
  }, [result]);

  const nodesDesc = useMemo(
    () => descriptor(nodeRows(result?.nodes ?? [])),
    [result],
  );
  const edgesDesc = useMemo(
    () => descriptor(edgeRows(result?.edges ?? [])),
    [result],
  );

  const seedId = result?.seed_id ?? "";
  const nodeCount = result?.node_count ?? result?.nodes?.length ?? 0;
  const warnings: string[] = [];
  if (result?.truncated)
    warnings.push(
      `Lineage truncated to the ${nodeCount} nodes nearest the seed — lower depth or pick a direction for the full graph.`,
    );
  else if (nodeCount > 80)
    warnings.push(
      `${nodeCount} nodes in view — dense graph. Lower the depth or pick a single direction for a clearer read.`,
    );
  if (result?.error) warnings.push(result.error);

  return (
    <div className="dc-lineage">
      <div className="dc-lineage-controls">
        <div className="dc-control-group">
          <span className="dc-control-label">Direction</span>
          <SegmentedControl
            options={DIRECTION_OPTS.map((o) => ({ value: o.value, label: o.label }))}
            value={direction}
            onChange={setDirection}
            ariaLabel="Lineage direction"
            size="sm"
          />
        </div>
        <div className="dc-control-group">
          <span className="dc-control-label">Depth</span>
          <SegmentedControl
            options={DEPTH_OPTS.map((o) => ({ value: o.value, label: o.label }))}
            value={depth}
            onChange={setDepth}
            ariaLabel="Lineage depth"
            size="sm"
          />
        </div>
        {loading && <span className="dc-results-count">Loading lineage…</span>}
      </div>

      <WarningBanner warnings={warnings} />

      {error ? (
        <div className="dc-error">{error}</div>
      ) : loading || result === null ? (
        <div className="dc-lineage-canvas dc-lineage-msg">Loading lineage…</div>
      ) : nodeCount === 0 ? (
        <div className="dc-lineage-canvas dc-lineage-msg">No lineage found for this entity.</div>
      ) : (
        // Mount the graph ONLY once data is loaded, so ReactFlow's `fitView`
        // runs with nodes present (async loads used to leave the canvas empty
        // because fitView had already run on the empty initial mount). Keyed on
        // entity/direction/depth so it re-fits when those change.
        // Small graphs get a proportionate box instead of a huge empty grid;
        // scales ~4vh/node from 38vh up to the CSS cap of 58vh (min-height 360px
        // still holds via the stylesheet).
        <div className="dc-lineage-canvas" style={{ height: `${Math.min(58, Math.max(38, 30 + nodeCount * 4))}vh` }}>
          <LineageGraph
            key={`${entityName}|${direction}|${depth}`}
            nodes={nodesDesc}
            edges={edgesDesc}
            seedId={seedId}
            selectedNodeId={selectedNodeId}
            onSelectNode={setSelectedNodeId}
            onExpandNode={(id) => {
              const name = nameById.get(id) ?? id;
              if (name && name !== entityName) onOpenEntity(name, "model");
            }}
          />
        </div>
      )}
    </div>
  );
}
