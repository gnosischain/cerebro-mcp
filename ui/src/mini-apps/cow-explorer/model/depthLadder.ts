// Pure depth-ladder math for the pair depth chart (no React, no ECharts).
//
// Server contract (`pair_depth` dataset): one row per known open order;
// `price` is ALWAYS quote-per-base for BOTH sides; `side` = "ask" for orders
// selling the BASE token, "bid" for orders selling the QUOTE token. Flip and
// cumulation are client-side re-projections of those rows — the server never
// inverts anything.

import { finite, rowsToObjects, type RowDataset } from "../../shared/rowDataset";

export interface PairDepthOrder {
  orderUid: string;
  owner: string;
  kind: string;
  side: "bid" | "ask";
  /** Quote-per-base limit price (both sides). */
  price: number;
  /** Remaining amount in BASE units. */
  amountBase: number;
  /** Remaining amount in QUOTE units. */
  amountQuote: number;
  partiallyFillable: boolean;
  orderClass: string;
  creationDate: string;
  /** Unix seconds; null when the row carried no parseable expiry. */
  validTo: number | null;
  sellSymbol: string;
  buySymbol: string;
}

/** One plotted ladder level: cum = cumulative amount from best price outward
 * (QUOTE for bids, BASE for asks); orders = order count at this price. */
export interface LadderPoint {
  price: number;
  cum: number;
  orders: number;
}

export interface DepthLadder {
  /** Ascending by price for plotting; cumulated best-first (so cum decreases
   * as price rises). Cumulative amounts are in QUOTE units. */
  bids: LadderPoint[];
  /** Ascending by price, cumulated best-first. Cumulative amounts in BASE. */
  asks: LadderPoint[];
  mid: number | null;
  midKind: "two_sided" | "bid_only" | "ask_only" | null;
  /** bestBid > bestAsk. Legitimate on CoW (batch auctions can clear crossed
   * intents) — a badge for the UI, never an error. */
  crossed: boolean;
}

function toBool(value: unknown): boolean {
  return value === true || value === 1 || value === "true" || value === "1";
}

/** Parse the server `pair_depth` rows with finite-guards (mirrors the
 * defensive style of parseRows.ts): rows with a bad side, non-positive or
 * non-finite price, or non-positive amounts are dropped rather than plotted. */
export function parsePairDepth(dataset?: RowDataset): PairDepthOrder[] {
  return rowsToObjects(dataset).flatMap((row) => {
    const side = row.side === "bid" || row.side === "ask" ? row.side : null;
    const price = finite(row.price);
    const amountBase = finite(row.amount_base);
    const amountQuote = finite(row.amount_quote);
    if (!side || price === null || price <= 0) return [];
    if (amountBase === null || amountBase <= 0 || amountQuote === null || amountQuote <= 0) return [];
    return [{
      orderUid: String(row.order_uid ?? ""),
      owner: String(row.owner ?? ""),
      kind: String(row.kind ?? ""),
      side,
      price,
      amountBase,
      amountQuote,
      partiallyFillable: toBool(row.partially_fillable),
      orderClass: String(row.order_class ?? ""),
      creationDate: String(row.creation_date ?? ""),
      validTo: finite(row.valid_to),
      sellSymbol: String(row.sell_symbol ?? ""),
      buySymbol: String(row.buy_symbol ?? ""),
    }];
  });
}

/** Re-project the book onto the inverted pair (base <-> quote): price' = 1/p,
 * base/quote amounts swap, and sides swap (an order selling the old base
 * sells the new quote, i.e. becomes a bid). Order-level facts (kind, symbols,
 * identity) are untouched — this is a pure client re-projection. */
export function flipOrders(rows: PairDepthOrder[]): PairDepthOrder[] {
  return rows.flatMap((row) => {
    if (!(row.price > 0)) return [];
    return [{
      ...row,
      price: 1 / row.price,
      amountBase: row.amountQuote,
      amountQuote: row.amountBase,
      side: row.side === "bid" ? "ask" as const : "bid" as const,
    }];
  });
}

function ladderSide(
  rows: PairDepthOrder[],
  side: "bid" | "ask",
): LadderPoint[] {
  // Group same-price orders into one level, then cumulate best-first:
  // bids best = highest price (walk descending), asks best = lowest
  // (walk ascending). Bids cumulate QUOTE (what they offer), asks BASE.
  const levels = new Map<number, { amount: number; orders: number }>();
  for (const row of rows) {
    if (row.side !== side) continue;
    const amount = side === "bid" ? row.amountQuote : row.amountBase;
    const level = levels.get(row.price) ?? { amount: 0, orders: 0 };
    levels.set(row.price, { amount: level.amount + amount, orders: level.orders + 1 });
  }
  const bestFirst = [...levels.entries()].sort((a, b) =>
    side === "bid" ? b[0] - a[0] : a[0] - b[0],
  );
  let cum = 0;
  const points = bestFirst.map(([price, level]) => {
    cum += level.amount;
    return { price, cum, orders: level.orders };
  });
  // Emit ascending by price for plotting; asks already are.
  return side === "bid" ? points.reverse() : points;
}

export function buildDepthLadder(rows: PairDepthOrder[]): DepthLadder {
  const bids = ladderSide(rows, "bid");
  const asks = ladderSide(rows, "ask");
  const bestBid = bids.length ? bids[bids.length - 1].price : null;
  const bestAsk = asks.length ? asks[0].price : null;
  let mid: number | null = null;
  let midKind: DepthLadder["midKind"] = null;
  if (bestBid !== null && bestAsk !== null) {
    mid = (bestBid + bestAsk) / 2;
    midKind = "two_sided";
  } else if (bestBid !== null) {
    mid = bestBid;
    midKind = "bid_only";
  } else if (bestAsk !== null) {
    mid = bestAsk;
    midKind = "ask_only";
  }
  return {
    bids,
    asks,
    mid,
    midKind,
    crossed: bestBid !== null && bestAsk !== null && bestBid > bestAsk,
  };
}

/** Human count line, e.g. "5 orders (3 sell GNO, 2 sell WXDAI)" — asks sell
 * the BASE token, bids sell the QUOTE token. */
export function countSummary(
  rows: PairDepthOrder[],
  baseSymbol: string,
  quoteSymbol: string,
): string {
  const askCount = rows.filter((row) => row.side === "ask").length;
  const bidCount = rows.filter((row) => row.side === "bid").length;
  const total = askCount + bidCount;
  const noun = total === 1 ? "order" : "orders";
  if (total === 0) return "0 orders";
  return `${total} ${noun} (${askCount} sell ${baseSymbol}, ${bidCount} sell ${quoteSymbol})`;
}
