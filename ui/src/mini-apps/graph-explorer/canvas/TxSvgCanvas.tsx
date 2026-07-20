import { useId, type CSSProperties, type KeyboardEvent, type MouseEvent } from "react";
import {
  RING_MAX_RADIUS,
  RING_MIN_RADIUS,
  txRingPositions,
  type TxLegRow,
  type TxNodeRow,
  type TxPosition,
} from "../model/txLayout";
import { colorForTxNode, colorForTxToken } from "./txVisualEncoding";
export { colorForTxToken } from "./txVisualEncoding";

/**
 * A deliberately small transaction contract for the SVG renderer. The wire
 * parser can grow independently; the renderer only needs identity, ordering,
 * endpoints and display metadata.
 */
export interface TxSvgLeg {
  id: string;
  source: string;
  target: string;
  txHash?: string;
  tokenAddress?: string | null;
  symbol?: string | null;
  amount?: number | string | null;
  /** Exact integer amount emitted by the receipt log. */
  rawAmount?: string | null;
  amountUsd?: number | null;
  logIndex?: number;
  blockNumber?: number;
  transactionIndex?: number;
  blockTimestamp?: string;
  seq?: number;
  txRank?: number;
}

export interface TxSvgNode {
  id: string;
  label?: string;
  role?: string;
  flags?: string[];
}

/** One renderer instance always represents exactly one selected transaction. */
export interface TxSvgTransaction {
  txHash: string;
  nodes: TxSvgNode[];
  legs: TxSvgLeg[];
}

export interface TxSvgCanvasProps {
  transaction: TxSvgTransaction;
  selectedLegId?: string | null;
  selectedNodeId?: string | null;
  onSelectLeg?: (legId: string) => void;
  onSelectNode?: (nodeId: string) => void;
  onClearSelection?: () => void;
  ariaLabel?: string;
  className?: string;
  height?: number | string;
  style?: CSSProperties;
}

export type TxSvgLayoutKind = "ring" | "hub";
export type TxSvgPathKind = "arc" | "self-loop";

export interface TxSvgNodeGeometry extends TxPosition {
  node: TxSvgNode;
}

export interface TxSvgPathGeometry {
  leg: TxSvgLeg;
  kind: TxSvgPathKind;
  d: string;
  color: string;
  /** Signed displacement from the canonical source-to-target chord. */
  offset: number;
  canonicalPair: readonly [string, string];
}

export interface TxSvgGeometry {
  layout: TxSvgLayoutKind;
  dominantNodeId: string | null;
  nodes: TxSvgNodeGeometry[];
  paths: TxSvgPathGeometry[];
  viewBox: { x: number; y: number; width: number; height: number };
}

export const TX_ARC_STEP = 28;
export const TX_MIN_RECIPROCAL_ARC = 32;
export const TX_SELF_LOOP_MIN_RADIUS = 42;
export const TX_SELF_LOOP_STEP = 24;
export const TX_NODE_RADIUS = 24;

const finite = (value: number | undefined, fallback = 0): number =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;

const fixed = (value: number): string => {
  const rounded = Math.round(value * 1000) / 1000;
  return Object.is(rounded, -0) ? "0" : String(rounded);
};

const compareText = (a: string, b: string): number =>
  a < b ? -1 : a > b ? 1 : 0;

/** Chain-order sort with a stable forensic identity tie-breaker. */
export function stableTxLegSort(legs: readonly TxSvgLeg[]): TxSvgLeg[] {
  return [...legs].sort((a, b) => {
    const txRank = finite(a.txRank) - finite(b.txRank);
    if (txRank) return txRank;
    const logIndex = finite(a.logIndex) - finite(b.logIndex);
    if (logIndex) return logIndex;
    const seq = finite(a.seq) - finite(b.seq);
    if (seq) return seq;
    return compareText(a.id, b.id);
  });
}

function selectedLegs(transaction: TxSvgTransaction): TxSvgLeg[] {
  const txHash = transaction.txHash.toLowerCase();
  return stableTxLegSort(
    transaction.legs.filter(
      (leg) => !leg.txHash || leg.txHash.toLowerCase() === txHash,
    ),
  );
}

function participatingNodes(
  transaction: TxSvgTransaction,
  legs: readonly TxSvgLeg[],
): TxSvgNode[] {
  const supplied = new Map(transaction.nodes.map((node) => [node.id, node]));
  const participating = new Set<string>();
  for (const leg of legs) {
    participating.add(leg.source);
    participating.add(leg.target);
  }

  // A verified zero-leg transaction may still have a known participant. For
  // non-empty transactions, unrelated nodes from other loaded transactions are
  // intentionally excluded from this one-transaction renderer.
  const ids = participating.size
    ? [...participating]
    : transaction.nodes.map((node) => node.id);

  return [...new Set(ids)]
    .sort(compareText)
    .map((id) => supplied.get(id) ?? { id, label: id, role: "address" });
}

function firstAppearance(legs: readonly TxSvgLeg[]): Map<string, number> {
  const first = new Map<string, number>();
  legs.forEach((leg, index) => {
    if (!first.has(leg.source)) first.set(leg.source, index);
    if (!first.has(leg.target)) first.set(leg.target, index);
  });
  return first;
}

function degreeByNode(legs: readonly TxSvgLeg[]): Map<string, number> {
  const degree = new Map<string, number>();
  for (const leg of legs) {
    degree.set(leg.source, (degree.get(leg.source) ?? 0) + 1);
    if (leg.target !== leg.source) {
      degree.set(leg.target, (degree.get(leg.target) ?? 0) + 1);
    }
  }
  return degree;
}

function dominantNode(
  nodes: readonly TxSvgNode[],
  legs: readonly TxSvgLeg[],
): string | null {
  if (nodes.length < 8 || legs.length === 0) return null;
  const degree = degreeByNode(legs);
  const ranked = [...nodes].sort((a, b) => {
    const byDegree = (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0);
    return byDegree || compareText(a.id, b.id);
  });
  const winner = ranked[0];
  if (!winner) return null;
  return (degree.get(winner.id) ?? 0) / legs.length >= 0.6
    ? winner.id
    : null;
}

function toLayoutNodes(nodes: readonly TxSvgNode[], legs: readonly TxSvgLeg[]): TxNodeRow[] {
  const degree = degreeByNode(legs);
  return nodes.map((node) => ({
    id: node.id,
    label: node.label ?? node.id,
    role: node.role ?? "address",
    project: "",
    columnRank: 0,
    inUsd: 0,
    outUsd: 0,
    legCount: degree.get(node.id) ?? 0,
    flags: node.flags ?? [],
  }));
}

function toLayoutLegs(legs: readonly TxSvgLeg[]): TxLegRow[] {
  return legs.map((leg, index) => ({
    id: leg.id,
    source: leg.source,
    target: leg.target,
    txHash: leg.txHash ?? "",
    logIndex: finite(leg.logIndex, index),
    blockNumber: finite(leg.blockNumber),
    transactionIndex: finite(leg.transactionIndex),
    blockTimestamp: leg.blockTimestamp ?? "",
    tokenAddress: leg.tokenAddress ?? "",
    symbol: leg.symbol ?? "",
    amount:
      leg.amount == null || leg.amount === "" || !Number.isFinite(Number(leg.amount))
        ? null
        : Number(leg.amount),
    rawAmount: leg.rawAmount ?? (leg.amount == null ? "" : String(leg.amount)),
    amountUsd: leg.amountUsd ?? null,
    seq: finite(leg.seq, index),
    txRank: finite(leg.txRank),
    txStatus: "unknown",
  }));
}

function hubPositions(
  nodes: readonly TxSvgNode[],
  legs: readonly TxSvgLeg[],
  hubId: string,
): Map<string, TxPosition> {
  const positions = new Map<string, TxPosition>();
  positions.set(hubId, { x: 0, y: 0 });
  const first = firstAppearance(legs);
  const leaves = nodes
    .filter((node) => node.id !== hubId)
    .sort((a, b) => {
      const byAppearance =
        (first.get(a.id) ?? Number.MAX_SAFE_INTEGER) -
        (first.get(b.id) ?? Number.MAX_SAFE_INTEGER);
      return byAppearance || compareText(a.id, b.id);
    });
  const radius = Math.min(
    RING_MAX_RADIUS,
    Math.max(RING_MIN_RADIUS, RING_MIN_RADIUS + leaves.length * 34),
  );
  leaves.forEach((node, index) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * index) / Math.max(leaves.length, 1);
    positions.set(node.id, {
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius,
    });
  });
  return positions;
}

function pairKey(source: string, target: string): string {
  return source < target ? `${source}\u0000${target}` : `${target}\u0000${source}`;
}

function canonicalPair(source: string, target: string): readonly [string, string] {
  return source < target ? [source, target] : [target, source];
}

function offsetsFor(
  count: number,
  side: 1 | -1,
  hasBothDirections: boolean,
): number[] {
  if (hasBothDirections) {
    return Array.from(
      { length: count },
      (_, index) => side * (TX_MIN_RECIPROCAL_ARC + index * TX_ARC_STEP),
    );
  }
  return Array.from(
    { length: count },
    (_, index) => (index - (count - 1) / 2) * TX_ARC_STEP,
  );
}

function arcPath(
  leg: TxSvgLeg,
  offset: number,
  canonical: readonly [string, string],
  positions: ReadonlyMap<string, TxPosition>,
): string {
  const source = positions.get(leg.source);
  const target = positions.get(leg.target);
  const u = positions.get(canonical[0]);
  const v = positions.get(canonical[1]);
  if (!source || !target || !u || !v) return "";

  const dx = v.x - u.x;
  const dy = v.y - u.y;
  const length = Math.hypot(dx, dy) || 1;
  const midpoint = { x: (u.x + v.x) / 2, y: (u.y + v.y) / 2 };
  const normal = { x: -dy / length, y: dx / length };
  const control = {
    x: midpoint.x + normal.x * offset,
    y: midpoint.y + normal.y * offset,
  };

  // Stop at the node perimeter rather than its centre. Besides preventing the
  // line from running through the node, this keeps the destination marker in
  // front of the circle instead of hiding the arrowhead under it.
  const fromSource = { x: control.x - source.x, y: control.y - source.y };
  const sourceLength = Math.hypot(fromSource.x, fromSource.y) || 1;
  const intoTarget = { x: target.x - control.x, y: target.y - control.y };
  const targetLength = Math.hypot(intoTarget.x, intoTarget.y) || 1;
  const start = {
    x: source.x + (fromSource.x / sourceLength) * TX_NODE_RADIUS,
    y: source.y + (fromSource.y / sourceLength) * TX_NODE_RADIUS,
  };
  const end = {
    x: target.x - (intoTarget.x / targetLength) * (TX_NODE_RADIUS + 2),
    y: target.y - (intoTarget.y / targetLength) * (TX_NODE_RADIUS + 2),
  };
  return [
    `M ${fixed(start.x)} ${fixed(start.y)}`,
    `Q ${fixed(control.x)} ${fixed(control.y)}`,
    `${fixed(end.x)} ${fixed(end.y)}`,
  ].join(" ");
}

function selfLoopPath(position: TxPosition, radius: number): string {
  const start = { x: position.x - radius * 0.45, y: position.y - radius * 0.55 };
  const end = { x: position.x + radius * 0.45, y: position.y - radius * 0.55 };
  const c1 = { x: position.x - radius * 1.35, y: position.y - radius * 1.8 };
  const c2 = { x: position.x + radius * 1.35, y: position.y - radius * 1.8 };
  return [
    `M ${fixed(start.x)} ${fixed(start.y)}`,
    `C ${fixed(c1.x)} ${fixed(c1.y)}`,
    `${fixed(c2.x)} ${fixed(c2.y)}`,
    `${fixed(end.x)} ${fixed(end.y)}`,
  ].join(" ");
}

function routePaths(
  legs: readonly TxSvgLeg[],
  positions: ReadonlyMap<string, TxPosition>,
): TxSvgPathGeometry[] {
  const groups = new Map<string, TxSvgLeg[]>();
  for (const leg of legs) {
    const key = pairKey(leg.source, leg.target);
    groups.set(key, [...(groups.get(key) ?? []), leg]);
  }

  const paths: TxSvgPathGeometry[] = [];
  for (const key of [...groups.keys()].sort(compareText)) {
    const group = stableTxLegSort(groups.get(key) ?? []);
    const first = group[0];
    if (!first) continue;
    const canonical = canonicalPair(first.source, first.target);

    if (canonical[0] === canonical[1]) {
      const position = positions.get(canonical[0]);
      if (!position) continue;
      group.forEach((leg, index) => {
        const radius = TX_SELF_LOOP_MIN_RADIUS + index * TX_SELF_LOOP_STEP;
        paths.push({
          leg,
          kind: "self-loop",
          d: selfLoopPath(position, radius),
          color: colorForTxToken(leg.tokenAddress),
          offset: radius,
          canonicalPair: canonical,
        });
      });
      continue;
    }

    const forward = group.filter(
      (leg) => leg.source === canonical[0] && leg.target === canonical[1],
    );
    const reverse = group.filter(
      (leg) => leg.source === canonical[1] && leg.target === canonical[0],
    );
    const hasBothDirections = forward.length > 0 && reverse.length > 0;
    const append = (direction: readonly TxSvgLeg[], side: 1 | -1) => {
      const offsets = offsetsFor(direction.length, side, hasBothDirections);
      direction.forEach((leg, index) => {
        const offset = offsets[index] ?? 0;
        paths.push({
          leg,
          kind: "arc",
          d: arcPath(leg, offset, canonical, positions),
          color: colorForTxToken(leg.tokenAddress),
          offset,
          canonicalPair: canonical,
        });
      });
    };
    append(forward, 1);
    append(reverse, -1);
  }
  return paths;
}

function geometryViewBox(
  nodes: readonly TxSvgNodeGeometry[],
  paths: readonly TxSvgPathGeometry[],
): TxSvgGeometry["viewBox"] {
  if (!nodes.length) return { x: 0, y: 0, width: 1000, height: 600 };
  const xs = nodes.map((node) => node.x);
  const ys = nodes.map((node) => node.y);
  const largestLoop = paths.reduce(
    (maximum, path) => path.kind === "self-loop" ? Math.max(maximum, path.offset) : maximum,
    0,
  );
  const largestArc = paths.reduce(
    (maximum, path) => path.kind === "arc" ? Math.max(maximum, Math.abs(path.offset)) : maximum,
    0,
  );
  const padding = Math.max(
    100,
    largestLoop * 2 + TX_NODE_RADIUS,
    largestArc + TX_NODE_RADIUS + 20,
  );
  const minX = Math.min(...xs) - padding;
  const maxX = Math.max(...xs) + padding;
  const minY = Math.min(...ys) - padding;
  const maxY = Math.max(...ys) + padding;
  return {
    x: minX,
    y: minY,
    width: Math.max(maxX - minX, 320),
    height: Math.max(maxY - minY, 320),
  };
}

/** Pure, permutation-stable geometry used by the renderer and unit tests. */
export function buildTxSvgGeometry(transaction: TxSvgTransaction): TxSvgGeometry {
  const legs = selectedLegs(transaction);
  const nodes = participatingNodes(transaction, legs);
  const hubId = dominantNode(nodes, legs);
  const positions = hubId
    ? hubPositions(nodes, legs, hubId)
    : txRingPositions(toLayoutNodes(nodes, legs), toLayoutLegs(legs));
  const nodeGeometry = nodes
    .map((node) => ({ node, ...(positions.get(node.id) ?? { x: 0, y: 0 }) }))
    .sort((a, b) => compareText(a.node.id, b.node.id));
  const paths = routePaths(legs, positions);
  return {
    layout: hubId ? "hub" : "ring",
    dominantNodeId: hubId,
    nodes: nodeGeometry,
    paths,
    viewBox: geometryViewBox(nodeGeometry, paths),
  };
}

function shortIdentifier(value: string): string {
  return value.length <= 18 ? value : `${value.slice(0, 8)}…${value.slice(-6)}`;
}

function legLabel(leg: TxSvgLeg): string {
  const token = leg.symbol || leg.tokenAddress || "unknown token";
  const amount = leg.rawAmount
    ? `${leg.rawAmount} raw units`
    : leg.amount == null
      ? "unknown amount"
      : String(leg.amount);
  const usd = leg.amountUsd == null ? "USD unknown" : `$${leg.amountUsd.toFixed(2)}`;
  const log = leg.logIndex == null ? "unknown log" : `log ${leg.logIndex}`;
  return `${log}: ${amount} ${token}, ${leg.source} to ${leg.target}, ${usd}`;
}

function activateOnKeyboard(
  event: KeyboardEvent<SVGGElement | SVGPathElement>,
  activate: () => void,
): void {
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  event.stopPropagation();
  activate();
}

/**
 * SVG transaction overview. The ordered leg table remains the primary reading
 * surface; this renderer guarantees that every selected transaction leg has a
 * separately visible and selectable path.
 */
export function TxSvgCanvas({
  transaction,
  selectedLegId,
  selectedNodeId,
  onSelectLeg,
  onSelectNode,
  onClearSelection,
  ariaLabel,
  className,
  height = 520,
  style,
}: TxSvgCanvasProps) {
  const geometry = buildTxSvgGeometry(transaction);
  const markerPrefix = `tx-arrow-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const colors = [...new Set(geometry.paths.map((path) => path.color))];
  const { x, y, width, height: viewHeight } = geometry.viewBox;

  const stopAnd =
    (callback: () => void) => (event: MouseEvent<SVGElement>) => {
      event.stopPropagation();
      callback();
    };

  return (
    <svg
      className={className}
      role="img"
      aria-label={ariaLabel ?? `Transfer graph for ${transaction.txHash}`}
      viewBox={`${fixed(x)} ${fixed(y)} ${fixed(width)} ${fixed(viewHeight)}`}
      preserveAspectRatio="xMidYMid meet"
      width="100%"
      height={height}
      style={{ display: "block", maxWidth: "100%", ...style }}
      data-layout={geometry.layout}
      onClick={onClearSelection}
    >
      <style>{`
        .tx-svg-hit:focus { stroke: rgba(37, 99, 235, .42); outline: none; }
        .tx-svg-node:focus circle { stroke: #2563eb; stroke-width: 5; outline: none; }
      `}</style>
      <defs>
        {colors.map((color) => (
          <marker
            id={`${markerPrefix}-${color.slice(1)}`}
            key={color}
            markerWidth="8"
            markerHeight="8"
            refX="7"
            refY="4"
            orient="auto"
            markerUnits="strokeWidth"
            viewBox="0 0 8 8"
          >
            <path d="M 0 0 L 8 4 L 0 8 z" fill={color} />
          </marker>
        ))}
      </defs>

      {geometry.paths.map((path) => {
        const selected = path.leg.id === selectedLegId;
        const label = legLabel(path.leg);
        return (
          <g key={path.leg.id} data-leg-id={path.leg.id}>
            {selected && (
              <path
                d={path.d}
                fill="none"
                stroke="#0f172a"
                strokeOpacity="0.25"
                strokeWidth="10"
                pointerEvents="none"
              />
            )}
            <path
              d={path.d}
              fill="none"
              stroke={path.color}
              strokeWidth={selected ? 4.5 : 2.75}
              markerEnd={`url(#${markerPrefix}-${path.color.slice(1)})`}
              pointerEvents="none"
            >
              <title>{label}</title>
            </path>
            <path
              className="tx-svg-hit"
              d={path.d}
              fill="none"
              stroke="transparent"
              strokeWidth="18"
              pointerEvents="stroke"
              cursor={onSelectLeg ? "pointer" : "default"}
              role={onSelectLeg ? "button" : undefined}
              tabIndex={onSelectLeg ? 0 : undefined}
              aria-label={label}
              aria-pressed={onSelectLeg ? selected : undefined}
              onClick={onSelectLeg ? stopAnd(() => onSelectLeg(path.leg.id)) : undefined}
              onKeyDown={
                onSelectLeg
                  ? (event) => activateOnKeyboard(event, () => onSelectLeg(path.leg.id))
                  : undefined
              }
            >
              <title>{label}</title>
            </path>
          </g>
        );
      })}

      {geometry.nodes.map(({ node, x: nodeX, y: nodeY }) => {
        const interactive = Boolean(onSelectNode);
        const selected = node.id === selectedNodeId;
        const label = node.label || shortIdentifier(node.id);
        return (
          <g
            className="tx-svg-node"
            key={node.id}
            data-node-id={node.id}
            role={interactive ? "button" : undefined}
            tabIndex={interactive ? 0 : undefined}
            aria-label={interactive ? `Inspect ${node.id}` : undefined}
            aria-pressed={interactive ? selected : undefined}
            cursor={interactive ? "pointer" : "default"}
            transform={`translate(${fixed(nodeX)} ${fixed(nodeY)})`}
            onClick={onSelectNode ? stopAnd(() => onSelectNode(node.id)) : undefined}
            onKeyDown={
              onSelectNode
                ? (event) => activateOnKeyboard(event, () => onSelectNode(node.id))
                : undefined
            }
          >
            <title>{node.id}</title>
            <circle
              r={TX_NODE_RADIUS}
              fill={colorForTxNode(node)}
              stroke={selected ? "#2563eb" : "#475569"}
              strokeWidth={selected ? "5" : "2"}
            />
            <text
              y={TX_NODE_RADIUS + 18}
              textAnchor="middle"
              fill="#0f172a"
              fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
              fontSize="14"
              fontWeight="600"
              pointerEvents="none"
            >
              {shortIdentifier(label)}
            </text>
          </g>
        );
      })}

      {geometry.paths.length === 0 && (
        <text
          x={x + width / 2}
          y={y + viewHeight / 2}
          textAnchor="middle"
          fill="#64748b"
          fontFamily="ui-sans-serif, system-ui, sans-serif"
          fontSize="18"
        >
          No ERC-20 transfer legs in this transaction
        </text>
      )}
    </svg>
  );
}

export default TxSvgCanvas;
