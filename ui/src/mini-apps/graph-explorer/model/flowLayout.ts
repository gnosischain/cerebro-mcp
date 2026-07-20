// FLOWS mode layout + parsing. Deterministic left-to-right layered layout —
// money flows rightward: upstream funders (negative hop_rank) left, seeds
// (rank 0) middle, downstream (positive) right. NO force sim and NO dagre:
// hop_rank IS the rank, so a manual barycenter pass is smaller and can never
// disagree with the server's rank assignment.
//
// Flow node rows: [id, label, sector, project, hop_rank, in_usd, out_usd,
//                  first_seen, last_seen, flags[]]
// Flow edge rows: [id, source, target, edge_class, token_address, symbol,
//                  amount, amount_usd, transfer_count, first_seen, last_seen,
//                  unknown_usd_rows]

import { buildGraphModel, SPACE_SIZE, shortId, type GraphModel } from "./parseRows";

export interface FlowNodeRow {
  id: string;
  label: string;
  sector: string;
  project: string;
  hopRank: number;
  /** Null means this direction has transfers but none has a known USD value. */
  inUsd: number | null;
  outUsd: number | null;
  firstSeen: string;
  lastSeen: string;
  flags: string[];
}

export interface FlowEdgeRow {
  id: string;
  source: string;
  target: string;
  edgeClass: string;
  tokenAddress: string;
  symbol: string;
  amount: number | null;
  amountUsd: number | null;
  transferCount: number;
  firstSeen: string;
  lastSeen: string;
  /** Daily source rows in this aggregate that lacked price enrichment. */
  unknownUsdRows: number;
}

function parseFlags(raw: unknown): string[] {
  if (Array.isArray(raw)) return raw.map(String);
  if (typeof raw === "string" && raw.startsWith("[")) {
    try {
      const parsed = JSON.parse(raw.replace(/'/g, '"'));
      return Array.isArray(parsed) ? parsed.map(String) : [];
    } catch {
      return [];
    }
  }
  return [];
}

function nullableNumber(raw: unknown): number | null {
  if (raw == null || raw === "") return null;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

export function parseFlowNodeRows(rows: unknown[][] | undefined): FlowNodeRow[] {
  const out: FlowNodeRow[] = [];
  for (const row of rows ?? []) {
    if (!Array.isArray(row) || !row[0]) continue;
    out.push({
      id: String(row[0]),
      label: String(row[1] ?? "") || shortId(String(row[0])),
      sector: String(row[2] ?? ""),
      project: String(row[3] ?? ""),
      hopRank: Number(row[4] ?? 0) || 0,
      inUsd: nullableNumber(row[5]),
      outUsd: nullableNumber(row[6]),
      firstSeen: String(row[7] ?? ""),
      lastSeen: String(row[8] ?? ""),
      flags: parseFlags(row[9]),
    });
  }
  return out;
}

export function parseFlowEdgeRows(rows: unknown[][] | undefined): FlowEdgeRow[] {
  const out: FlowEdgeRow[] = [];
  for (const row of rows ?? []) {
    if (!Array.isArray(row) || !row[0] || !row[1] || !row[2]) continue;
    const amount = row[6];
    const amountUsd = row[7];
    out.push({
      id: String(row[0]),
      source: String(row[1]),
      target: String(row[2]),
      edgeClass: String(row[3] ?? "transfer"),
      tokenAddress: String(row[4] ?? ""),
      symbol: String(row[5] ?? ""),
      amount: amount === null || amount === undefined || amount === "" ? null : Number(amount),
      amountUsd:
        amountUsd === null || amountUsd === undefined || amountUsd === ""
          ? null
          : Number(amountUsd),
      transferCount: Number(row[8] ?? 0) || 0,
      firstSeen: String(row[9] ?? ""),
      lastSeen: String(row[10] ?? ""),
      unknownUsdRows: Math.max(0, Number(row[11] ?? (amountUsd == null ? 1 : 0)) || 0),
    });
  }
  return out;
}

// ---- Layered layout --------------------------------------------------------

const MARGIN_X = SPACE_SIZE * 0.08;
const MARGIN_Y = SPACE_SIZE * 0.08;
/** Vertical spacing cap so 3-node layers don't spread across the full space. */
export const Y_STEP_CAP = 220;
const BARYCENTER_SWEEPS = 4;
/** Amplitude of the deterministic vertical undulation applied to each layer's
 * center. Without it, a mostly-linear trace (every hop a single node) places
 * all nodes at the vertical center — a dead-straight horizontal line whose
 * edges and arrowheads overlap into an unreadable tangle. The wave stays well
 * inside the vertical margins so multi-node layers still spread cleanly. */
export const WAVE_AMPLITUDE = SPACE_SIZE * 0.16;

export interface FlowPosition {
  x: number;
  y: number;
}

/** Deterministic layered positions keyed by node id. Layer = hop_rank
 * (sorted ascending → left-to-right); order within a layer starts USD-desc
 * (then id) and is refined by fixed barycenter sweeps (L→R, R→L, ×2) that
 * reduce edge crossings. Pure function of the (nodes, edges) SETS — input
 * row order never changes the output. */
export function layeredFlowPositions(
  nodes: FlowNodeRow[],
  edges: FlowEdgeRow[],
): Map<string, FlowPosition> {
  const positions = new Map<string, FlowPosition>();
  if (!nodes.length) return positions;

  const ranks = Array.from(new Set(nodes.map((n) => n.hopRank))).sort(
    (a, b) => a - b,
  );
  const layerIndex = new Map<number, number>(ranks.map((r, i) => [r, i]));

  // Initial per-layer order: intrinsic sort keys only (permutation-invariant).
  const layers: FlowNodeRow[][] = ranks.map(() => []);
  for (const n of nodes) layers[layerIndex.get(n.hopRank)!].push(n);
  for (const layer of layers) {
    layer.sort((a, b) => {
      const usd =
        (b.inUsd ?? 0) + (b.outUsd ?? 0) -
        ((a.inUsd ?? 0) + (a.outUsd ?? 0));
      return usd !== 0 ? usd : a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
  }

  // Undirected adjacency for the barycenter heuristic.
  const neighbors = new Map<string, string[]>();
  for (const e of edges) {
    if (!neighbors.has(e.source)) neighbors.set(e.source, []);
    if (!neighbors.has(e.target)) neighbors.set(e.target, []);
    neighbors.get(e.source)!.push(e.target);
    neighbors.get(e.target)!.push(e.source);
  }
  const nodeLayer = new Map<string, number>();
  nodes.forEach((n) => nodeLayer.set(n.id, layerIndex.get(n.hopRank)!));

  const sweep = (leftToRight: boolean) => {
    const order = leftToRight
      ? Array.from({ length: layers.length }, (_, i) => i).slice(1)
      : Array.from({ length: layers.length }, (_, i) => i)
          .slice(0, -1)
          .reverse();
    for (const li of order) {
      const refLayer = leftToRight ? li - 1 : li + 1;
      const refIndex = new Map<string, number>();
      layers[refLayer].forEach((n, i) => refIndex.set(n.id, i));
      const current = layers[li];
      const currentIndex = new Map<string, number>();
      current.forEach((n, i) => currentIndex.set(n.id, i));
      const bary = new Map<string, number>();
      for (const n of current) {
        const refs = (neighbors.get(n.id) ?? []).filter(
          (m) => nodeLayer.get(m) === refLayer,
        );
        if (!refs.length) {
          bary.set(n.id, currentIndex.get(n.id)!);
          continue;
        }
        let sum = 0;
        for (const m of refs) sum += refIndex.get(m) ?? 0;
        bary.set(n.id, sum / refs.length);
      }
      // Stable: ties keep the current relative order.
      current.sort((a, b) => {
        const d = bary.get(a.id)! - bary.get(b.id)!;
        return d !== 0 ? d : currentIndex.get(a.id)! - currentIndex.get(b.id)!;
      });
    }
  };
  for (let s = 0; s < BARYCENTER_SWEEPS; s++) sweep(s % 2 === 0);

  const xStep =
    layers.length > 1 ? (SPACE_SIZE - 2 * MARGIN_X) / (layers.length - 1) : 0;
  // Undulate the per-layer vertical center so a linear chain reads as a gentle
  // zig-zag instead of an overlapping flat line. Skipped for <=2 layers (a
  // seed-plus-one-hop view reads fine flat). Deterministic in layer index →
  // permutation-invariant.
  const wave = layers.length > 2;
  const clampY = (y: number) =>
    Math.max(MARGIN_Y, Math.min(SPACE_SIZE - MARGIN_Y, y));
  for (let li = 0; li < layers.length; li++) {
    const layer = layers[li];
    const x = layers.length > 1 ? MARGIN_X + li * xStep : SPACE_SIZE / 2;
    const centerY =
      SPACE_SIZE / 2 + (wave ? Math.sin(li * 0.95) * WAVE_AMPLITUDE : 0);
    const yStep =
      layer.length > 1
        ? Math.min(Y_STEP_CAP, (SPACE_SIZE - 2 * MARGIN_Y) / (layer.length - 1))
        : 0;
    const y0 = centerY - (yStep * (layer.length - 1)) / 2;
    layer.forEach((n, i) => positions.set(n.id, { x, y: clampY(y0 + i * yStep) }));
  }
  return positions;
}

// ---- Model adapter ---------------------------------------------------------

/** Sectors → palette kinds (COLOR_BY_KIND keys). Unknown sectors fall back to
 * plain address coloring so new label sectors degrade gracefully. */
const SECTOR_KINDS = new Set([
  "bridges",
  "dex",
  "privacy",
  "payments",
  "lending",
  "staking",
  "cex",
]);

function sectorKind(sector: string): string {
  const key = sector.trim().toLowerCase().replace(/\s+/g, "_");
  return SECTOR_KINDS.has(key) ? key : "address";
}

/** An address flagged `token_contract` is an ERC-20 / vault contract, NOT a
 * counterparty — a transfer into it is a deposit/burn/redeem. Render it as a
 * distinct "token" kind so it never reads as "money sent to a person". */
function nodeKind(n: FlowNodeRow): string {
  return n.flags.includes("token_contract") ? "token" : sectorKind(n.sector);
}

export interface FlowGraph {
  model: GraphModel;
  nodes: FlowNodeRow[];
  edges: FlowEdgeRow[];
}

/** Adapt flow datasets into the shared GraphModel (canvas contract), then
 * overwrite positions with the deterministic layered layout. Edge weight :=
 * amount_usd (log10 width for free); bridge edges (NULL USD) get weight 0 →
 * minimum width. Edge "profile" := token symbol (or "bridge") so the legend
 * and edge coloring group by token. */
export function buildFlowGraphModel(
  flowNodeRows: unknown[][] | undefined,
  flowEdgeRows: unknown[][] | undefined,
): FlowGraph {
  const nodes = parseFlowNodeRows(flowNodeRows);
  const edges = parseFlowEdgeRows(flowEdgeRows);
  const mappedNodes = nodes.map((n) => [
    n.id,
    nodeKind(n),
    n.label,
    [] as string[],
  ]);
  const mappedEdges = edges.map((e) => [
    e.id,
    e.source,
    e.target,
    e.edgeClass === "bridge" ? "bridge" : e.symbol || "transfer",
    e.amountUsd ?? 0,
    e.transferCount,
    true, // flow edges are always directed
  ]);
  const model = buildGraphModel(mappedNodes, mappedEdges, [], {
    // Same pair, same direction, different token/leg — a rendering
    // artefact, not distinct topology. Bundle so they stop stacking
    // invisibly on one another.
    collapseParallel: true,
  });
  const positions = layeredFlowPositions(nodes, edges);
  for (let i = 0; i < model.n; i++) {
    const p = positions.get(model.indexToId[i]);
    if (p) {
      model.positions[i * 2] = p.x;
      model.positions[i * 2 + 1] = p.y;
    }
  }
  return { model, nodes, edges };
}
