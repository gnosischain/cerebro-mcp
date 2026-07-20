// Transactions mode: parsing + deterministic column layout for transfer legs.
//
// The unit is a LEG — one (transaction_hash, log_index) row. Legs are never
// merged, because the signature of a swap, a batch settlement or a drain lives
// in the ORDER and ADJACENCY of legs inside one transaction, which is exactly
// what aggregation destroys.
//
// A node-link canvas holds one position per node, so it cannot draw a true
// sequence ladder (an address appears at many log indices). The canvas
// therefore shows STRUCTURE — who touched whom — while the ordered leg rail
// beside it carries the sequence. Reading order lives in the rail; the canvas
// is the overview.

import { buildGraphModel, SPACE_SIZE, type GraphModel } from "./parseRows";

export interface TxLegRow {
  id: string;
  source: string;
  target: string;
  txHash: string;
  logIndex: number;
  blockNumber: number;
  transactionIndex: number;
  blockTimestamp: string;
  tokenAddress: string;
  symbol: string;
  /** Normalized token amount; null when token decimals are unavailable. */
  amount: number | null;
  /** Integer token amount as emitted in the Transfer log, when published. */
  rawAmount: string;
  amountUsd: number | null;
  /** Global ordinal across the loaded legs, already in chain order. */
  seq: number;
  /** Which transaction (0-based, in chain order) this leg belongs to. */
  txRank: number;
  txStatus: string;
}

export interface TxNodeRow {
  id: string;
  label: string;
  role: string;
  project: string;
  columnRank: number;
  inUsd: number | null;
  outUsd: number | null;
  legCount: number;
  flags: string[];
}

/** Address-discovery result. This is intentionally separate from receipt
 * legs: a zero-row discovery is not a transaction with zero legs. */
export interface TxListRow {
  txHash: string;
  blockNumber: number;
  transactionIndex: number;
  blockTimestamp: string;
  legCount: number;
  tokenCount: number;
}

const num = (v: unknown): number => {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
};
const nullableNum = (v: unknown): number | null => {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
};
const str = (v: unknown): string => (v == null ? "" : String(v));

export function parseTxNodeRows(rows: unknown[][] | undefined): TxNodeRow[] {
  if (!Array.isArray(rows)) return [];
  return rows
    .filter((r) => Array.isArray(r) && r[0])
    .map((r) => ({
      id: str(r[0]),
      label: str(r[1]),
      role: str(r[2]),
      project: str(r[3]),
      columnRank: num(r[4]),
      inUsd: nullableNum(r[5]),
      outUsd: nullableNum(r[6]),
      legCount: num(r[7]),
      flags: Array.isArray(r[8]) ? (r[8] as string[]).map(str) : [],
    }));
}

export function parseTxLegRows(rows: unknown[][] | undefined): TxLegRow[] {
  if (!Array.isArray(rows)) return [];
  return rows
    .filter((r) => Array.isArray(r) && r[0])
    .map((r) => ({
      id: str(r[0]),
      source: str(r[1]),
      target: str(r[2]),
      txHash: str(r[3]),
      logIndex: num(r[4]),
      blockNumber: num(r[5]),
      transactionIndex: num(r[6]),
      blockTimestamp: str(r[7]),
      tokenAddress: str(r[8]),
      symbol: str(r[9]),
      amount: nullableNum(r[10]),
      rawAmount: str(r[15] ?? r[10]),
      amountUsd: nullableNum(r[11]),
      seq: num(r[12]),
      txRank: num(r[13]),
      txStatus: str(r[14]) || "unknown",
    }));
}

export function parseTxListRows(rows: unknown[][] | undefined): TxListRow[] {
  if (!Array.isArray(rows)) return [];
  const byHash = new Map<string, TxListRow>();
  for (const row of rows) {
    if (!Array.isArray(row) || !row[0]) continue;
    const parsed: TxListRow = {
      txHash: str(row[0]).toLowerCase(),
      blockNumber: num(row[1]),
      transactionIndex: num(row[2]),
      blockTimestamp: str(row[3]),
      legCount: num(row[4]),
      tokenCount: num(row[5]),
    };
    const previous = byHash.get(parsed.txHash);
    if (
      !previous ||
      parsed.blockNumber > previous.blockNumber ||
      (parsed.blockNumber === previous.blockNumber &&
        parsed.transactionIndex > previous.transactionIndex)
    ) {
      byHash.set(parsed.txHash, parsed);
    }
  }
  return [...byHash.values()].sort((a, b) =>
    b.blockNumber - a.blockNumber ||
    b.transactionIndex - a.transactionIndex ||
    (a.txHash < b.txHash ? -1 : a.txHash > b.txHash ? 1 : 0),
  );
}

/** Canvas colour bucket. `token` and `burn` are deliberately distinct from
 * `address`: a leg whose endpoint is an ERC-20 contract or the zero address is
 * a mint/burn/reserve payout, NOT a payment to a counterparty. Rendering the
 * two alike is the confusion that invalidated an earlier investigation. */
export function txNodeKind(n: TxNodeRow): string {
  if (n.role === "burn" || n.flags.includes("burn_address")) return "burn";
  if (n.role === "token" || n.flags.includes("token_contract")) return "token";
  if (n.role === "seed" || n.flags.includes("seed")) return "seed";
  return "address";
}

export interface TxPosition {
  x: number;
  y: number;
}

/** Ring geometry. Radius grows with participant count so chords stay
 * separated, capped so a big transaction still frames in one screenful. */
export const RING_MIN_RADIUS = 240;
export const RING_STEP = 46;
export const RING_MAX_RADIUS = 1400;

/** Deterministic RING positions, ordered clockwise from the top by lane then
 * first appearance (chain order), so the ring reads in execution order.
 *
 * Lanes keep structural endpoints together: token contracts first, then the
 * actual actors, then burn addresses — they are sources/sinks, not middlemen,
 * and scattering them through the ring makes a mint look like a hop.
 *
 * Pure function of the (nodes, legs) SETS — input row order never changes the
 * output, so the same transaction always draws identically and a screenshot is
 * reproducible evidence.
 */
export function txRingPositions(
  nodes: TxNodeRow[],
  legs: TxLegRow[],
): Map<string, TxPosition> {
  const positions = new Map<string, TxPosition>();
  if (!nodes.length) return positions;

  // First appearance in chain order.
  const firstSeen = new Map<string, number>();
  for (const leg of [...legs].sort((a, b) => a.seq - b.seq)) {
    for (const id of [leg.source, leg.target]) {
      if (!firstSeen.has(id)) firstSeen.set(id, leg.seq);
    }
  }

  const laneOf = (n: TxNodeRow): number => {
    const kind = txNodeKind(n);
    if (kind === "burn") return 2; // sink arc
    if (kind === "token") return 0; // source arc
    return 1; // the actual actors
  };

  const sorted = [...nodes].sort((a, b) => {
    const la = laneOf(a);
    const lb = laneOf(b);
    if (la !== lb) return la - lb;
    const fa = firstSeen.get(a.id) ?? Number.MAX_SAFE_INTEGER;
    const fb = firstSeen.get(b.id) ?? Number.MAX_SAFE_INTEGER;
    if (fa !== fb) return fa - fb;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });

  // RING, not columns. Columns placed every participant on a near-horizontal
  // line, so edges between different pairs ran along a shared axis and piled
  // on top of each other — and curvature cannot separate collinear edges,
  // because the curve is a fixed offset on the line's own normal. On a ring
  // each pair gets its own chord at its own angle, so distinct edges are
  // distinct lines, and reciprocal pairs bow to opposite sides.
  //
  // Angular order is still first-appearance order (clockwise from the top),
  // so the ring is read in execution order — while the rail keeps the exact
  // log_index sequence.
  const n = sorted.length;
  const center = SPACE_SIZE / 2;
  if (n === 1) {
    positions.set(sorted[0].id, { x: center, y: center });
    return positions;
  }
  // Radius grows with participant count so chords stay separated, but stays
  // inside one screenful after an auto-fit.
  const radius = Math.min(RING_MAX_RADIUS, RING_MIN_RADIUS + n * RING_STEP);
  sorted.forEach((node, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / n;
    positions.set(node.id, {
      x: center + Math.cos(angle) * radius,
      y: center + Math.sin(angle) * radius,
    });
  });
  return positions;
}

export interface TxGraph {
  model: GraphModel;
  nodes: TxNodeRow[];
  legs: TxLegRow[];
}

export function buildTxGraphModel(
  txNodeRows: unknown[][] | undefined,
  txLegRows: unknown[][] | undefined,
): TxGraph {
  const nodes = parseTxNodeRows(txNodeRows);
  const legs = parseTxLegRows(txLegRows);
  const mappedNodes = nodes.map((n) => [
    n.id,
    txNodeKind(n),
    n.label,
    [] as string[],
  ]);
  // One canvas edge per LEG — deliberately not deduplicated by (src,tgt), so
  // repeated transfers between the same pair stay visible as separate legs and
  // the edge count matches the rail.
  const mappedLegs = legs.map((l) => [
    l.id,
    l.source,
    l.target,
    l.symbol || "transfer",
    // Keep domain truth nullable. The renderer may impose a minimum visual
    // width, but an unpriced transfer must never leak into a textual surface as
    // the fabricated value `1`.
    l.amountUsd,
    1,
    true,
  ]);
  const model = buildGraphModel(mappedNodes, mappedLegs, [], {
    // Transaction Detail renders every receipt leg as a distinct SVG path.
    // Keeping the model uncollapsed also preserves exact leg selection and
    // the one-row/one-edge invariant in the inspector/table fallback.
    collapseParallel: false,
  });
  const positions = txRingPositions(nodes, legs);
  for (let i = 0; i < model.n; i++) {
    const p = positions.get(model.indexToId[i]);
    if (p) {
      model.positions[i * 2] = p.x;
      model.positions[i * 2 + 1] = p.y;
    }
  }
  return { model, nodes, legs };
}

/** Group legs into transactions, preserving chain order within and between. */
export interface TxGroup {
  txHash: string;
  blockNumber: number;
  blockTimestamp: string;
  legs: TxLegRow[];
  /** Null when any leg lacks a price; unknown is never represented as zero. */
  totalUsd: number | null;
  knownUsdTotal: number;
  unpricedLegCount: number;
  tokens: string[];
}

export function groupLegsByTx(legs: TxLegRow[]): TxGroup[] {
  const byHash = new Map<string, TxGroup>();
  for (const leg of [...legs].sort((a, b) => a.seq - b.seq)) {
    let g = byHash.get(leg.txHash);
    if (!g) {
      g = {
        txHash: leg.txHash,
        blockNumber: leg.blockNumber,
        blockTimestamp: leg.blockTimestamp,
        legs: [],
        totalUsd: 0,
        knownUsdTotal: 0,
        unpricedLegCount: 0,
        tokens: [],
      };
      byHash.set(leg.txHash, g);
    }
    g.legs.push(leg);
    if (leg.amountUsd == null) {
      g.unpricedLegCount += 1;
      g.totalUsd = null;
    } else {
      g.knownUsdTotal += leg.amountUsd;
      if (g.totalUsd !== null) g.totalUsd += leg.amountUsd;
    }
    if (leg.symbol && !g.tokens.includes(leg.symbol)) g.tokens.push(leg.symbol);
  }
  return Array.from(byHash.values());
}
