// Hand-rolled SVG sankey in the visual language of graph-explorer's
// MoneySankey (vector <text> labels, themed CSS-var ribbons, crisp at any
// zoom/DPR) generalized for BIPARTITE flows (left column → right column).
//
// Why not ECharts sankey: its node heights are strictly value-proportional,
// so one dominant flow renders as a giant bar with hairline ribbons and
// unreadable labels (observed in production). This layout compresses node
// heights with sqrt scaling and enforces a minimum height, keeping every
// node and label readable while ribbon width still communicates magnitude.

import { useMemo, useState } from "react";

export interface SankeyLinkDatum {
  source: string;
  target: string;
  value: number;
}

export interface SankeySvgProps {
  links: SankeyLinkDatum[];
  /** Optional display label per node id (defaults to the id). */
  nodeLabel?: (id: string, side: "left" | "right") => string;
  /** Value formatter for node/ribbon annotations. */
  formatValue?: (value: number) => string;
  leftTitle?: string;
  rightTitle?: string;
  onNodeClick?: (id: string, side: "left" | "right") => void;
  /** Max nodes per column; the rest is aggregated into "Other". */
  maxPerSide?: number;
}

interface LayoutNode {
  id: string;
  side: "left" | "right";
  label: string;
  value: number;
  y: number;
  height: number;
}

const WIDTH = 960;
const NODE_W = 14;
const LABEL_W = 240;
const MIN_H = 20;
const GAP = 8;
const TOP = 34;

function scaledHeights(values: number[], available: number): number[] {
  const scaled = values.map((value) => Math.sqrt(Math.max(0, value)));
  const total = scaled.reduce((acc, v) => acc + v, 0) || 1;
  const raw = scaled.map((v) => (v / total) * available);
  return raw.map((height) => Math.max(MIN_H, height));
}

export function SankeySvg({
  links,
  nodeLabel = (id) => id,
  formatValue = (value) => value.toLocaleString(),
  leftTitle,
  rightTitle,
  onNodeClick,
  maxPerSide = 14,
}: SankeySvgProps) {
  const [hovered, setHovered] = useState<string | null>(null);

  const layout = useMemo(() => {
    const sum = (side: "source" | "target") => {
      const totals = new Map<string, number>();
      for (const link of links) {
        const key = link[side];
        totals.set(key, (totals.get(key) ?? 0) + Math.max(0, link.value));
      }
      return totals;
    };
    const keep = (totals: Map<string, number>) => {
      const sorted = [...totals].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
      const kept = sorted.slice(0, maxPerSide).map(([id]) => id);
      return new Set(kept);
    };
    const leftTotals = sum("source");
    const rightTotals = sum("target");
    const keepLeft = keep(leftTotals);
    const keepRight = keep(rightTotals);
    const folded = new Map<string, SankeyLinkDatum>();
    for (const link of links) {
      const source = keepLeft.has(link.source) ? link.source : "__other_left__";
      const target = keepRight.has(link.target) ? link.target : "__other_right__";
      const key = `${source}|${target}`;
      const existing = folded.get(key);
      folded.set(key, { source, target, value: (existing?.value ?? 0) + Math.max(0, link.value) });
    }
    const flows = [...folded.values()].filter((link) => link.value > 0);
    const columnIds = (side: "source" | "target", otherId: string) => {
      const totals = new Map<string, number>();
      for (const link of flows) {
        const key = link[side];
        totals.set(key, (totals.get(key) ?? 0) + link.value);
      }
      return [...totals]
        .sort((a, b) => (a[0] === otherId ? 1 : b[0] === otherId ? -1 : b[1] - a[1]))
        .map(([id, value]) => ({ id, value }));
    };
    const leftIds = columnIds("source", "__other_left__");
    const rightIds = columnIds("target", "__other_right__");
    const rows = Math.max(leftIds.length, rightIds.length);
    const available = Math.max(240, rows * 34);
    const place = (
      items: Array<{ id: string; value: number }>,
      side: "left" | "right",
    ): LayoutNode[] => {
      const heights = scaledHeights(items.map((item) => item.value), available);
      let y = TOP;
      return items.map((item, index) => {
        const node: LayoutNode = {
          id: item.id,
          side,
          label:
            item.id === "__other_left__" || item.id === "__other_right__"
              ? "Other"
              : nodeLabel(item.id, side),
          value: item.value,
          y,
          height: heights[index],
        };
        y += heights[index] + GAP;
        return node;
      });
    };
    const left = place(leftIds, "left");
    const right = place(rightIds, "right");
    const height = Math.max(
      left.length ? left[left.length - 1].y + left[left.length - 1].height : TOP,
      right.length ? right[right.length - 1].y + right[right.length - 1].height : TOP,
    ) + 18;
    // Value-proportional FILLED ribbons: each ribbon occupies its share of the
    // node's height on both ends, so stacked ribbons cover the whole bar (a
    // stroked line only touched the top). A ribbon can taper because the two
    // nodes use sqrt-compressed heights — that's expected sankey behavior.
    const leftOffset = new Map<string, number>();
    const rightOffset = new Map<string, number>();
    const leftById = new Map(left.map((node) => [node.id, node]));
    const rightById = new Map(right.map((node) => [node.id, node]));
    // Order ribbons by source position then value so the left column's stack
    // reads top-to-bottom; target offsets accumulate in the same pass.
    const ordered = flows
      .map((flow) => ({ flow, srcNode: leftById.get(flow.source)!, tgtNode: rightById.get(flow.target)! }))
      .sort((a, b) => a.srcNode.y - b.srcNode.y || b.flow.value - a.flow.value);
    const ribbons = ordered.map(({ flow, srcNode, tgtNode }) => {
      const srcH = (flow.value / (srcNode.value || 1)) * srcNode.height;
      const tgtH = (flow.value / (tgtNode.value || 1)) * tgtNode.height;
      const so = leftOffset.get(flow.source) ?? 0;
      const to = rightOffset.get(flow.target) ?? 0;
      const sy0 = srcNode.y + so;
      const ty0 = tgtNode.y + to;
      leftOffset.set(flow.source, so + srcH);
      rightOffset.set(flow.target, to + tgtH);
      return { ...flow, sy0, sy1: sy0 + srcH, ty0, ty1: ty0 + tgtH };
    });
    return { left, right, ribbons, height };
  }, [links, maxPerSide, nodeLabel]);

  if (layout.ribbons.length === 0) return null;
  const x0 = LABEL_W;
  const x1 = WIDTH - LABEL_W - NODE_W;
  const mid = (x0 + NODE_W + x1) / 2;

  return (
    <svg
      className="sfl"
      viewBox={`0 0 ${WIDTH} ${layout.height}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
    >
      {leftTitle && <text className="sfl__title" x={x0 + NODE_W} y={18} textAnchor="end">{leftTitle}</text>}
      {rightTitle && <text className="sfl__title" x={x1} y={18}>{rightTitle}</text>}
      {layout.ribbons.map((ribbon) => {
        const id = `${ribbon.source}|${ribbon.target}`;
        const active = hovered === null || hovered === ribbon.source || hovered === ribbon.target;
        const xs = x0 + NODE_W;
        // Filled area: top edge source→target, down the target edge, bottom
        // edge back, close along the source edge.
        const d = `M ${xs} ${ribbon.sy0} C ${mid} ${ribbon.sy0}, ${mid} ${ribbon.ty0}, ${x1} ${ribbon.ty0}`
          + ` L ${x1} ${ribbon.ty1}`
          + ` C ${mid} ${ribbon.ty1}, ${mid} ${ribbon.sy1}, ${xs} ${ribbon.sy1} Z`;
        return (
          <path
            key={id}
            className={`sfl__ribbon${active ? "" : " sfl__ribbon--dim"}`}
            d={d}
            onMouseEnter={() => setHovered(ribbon.source)}
            onMouseLeave={() => setHovered(null)}
          >
            <title>{`${ribbon.source === "__other_left__" ? "Other" : nodeLabel(ribbon.source, "left")} → ${ribbon.target === "__other_right__" ? "Other" : nodeLabel(ribbon.target, "right")}: ${formatValue(ribbon.value)}`}</title>
          </path>
        );
      })}
      {[...layout.left, ...layout.right].map((node) => {
        const isLeft = node.side === "left";
        const x = isLeft ? x0 : x1;
        const clickable = Boolean(onNodeClick) && !node.id.startsWith("__other_");
        return (
          <g
            key={`${node.side}:${node.id}`}
            className={`sfl__node${clickable ? " sfl__node--clickable" : ""}`}
            onMouseEnter={() => setHovered(node.id)}
            onMouseLeave={() => setHovered(null)}
            onClick={clickable ? () => onNodeClick?.(node.id, node.side) : undefined}
            role={clickable ? "button" : undefined}
            tabIndex={clickable ? 0 : undefined}
            onKeyDown={clickable ? (event) => {
              if (event.key === "Enter" || event.key === " ") onNodeClick?.(node.id, node.side);
            } : undefined}
          >
            <rect x={x} y={node.y} width={NODE_W} height={node.height} rx={3} />
            <text
              className="sfl__label"
              x={isLeft ? x - 8 : x + NODE_W + 8}
              y={node.y + node.height / 2 + 3.5}
              textAnchor={isLeft ? "end" : "start"}
            >
              {node.label}
            </text>
            <text
              className="sfl__value"
              x={isLeft ? x - 8 : x + NODE_W + 8}
              y={node.y + node.height / 2 + 15}
              textAnchor={isLeft ? "end" : "start"}
            >
              {formatValue(node.value)}
            </text>
            <title>{`${node.label}: ${formatValue(node.value)}`}</title>
          </g>
        );
      })}
    </svg>
  );
}
