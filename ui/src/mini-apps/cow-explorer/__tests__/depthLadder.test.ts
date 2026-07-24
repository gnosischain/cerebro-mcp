import { describe, expect, it } from "vitest";

import {
  buildDepthLadder,
  countSummary,
  flipOrders,
  parsePairDepth,
  type PairDepthOrder,
} from "../model/depthLadder";
import {
  growthAccountingOption,
  pairDepthOption,
  stackedSeriesOption,
} from "../model/chartOptions";

// Mirrors the server `pair_depth` projection column order (cow_explorer.py).
const PAIR_DEPTH_COLUMNS = [
  "order_uid", "owner", "kind", "side", "order_class", "partially_fillable",
  "creation_date", "valid_to", "sell_token", "buy_token", "sell_symbol",
  "buy_symbol", "sell_decimals", "buy_decimals", "price", "amount_base",
  "amount_quote", "sell_amount_raw", "buy_amount_raw", "indexed_from",
  "indexed_to", "source_observed_at",
];

function depthRow(overrides: Record<string, unknown>): unknown[] {
  const base: Record<string, unknown> = {
    order_uid: "0xuid", owner: "0xowner", kind: "sell", side: "ask",
    order_class: "limit", partially_fillable: false,
    creation_date: "2026-07-20T10:00:00Z", valid_to: 1790000000,
    sell_token: "0xbase", buy_token: "0xquote",
    sell_symbol: "GNO", buy_symbol: "WXDAI",
    sell_decimals: 18, buy_decimals: 18,
    price: 100, amount_base: 1, amount_quote: 100,
    sell_amount_raw: "1000000000000000000", buy_amount_raw: "100000000000000000000",
    indexed_from: "2026-07-20T10:00:00Z", indexed_to: "2026-07-20T10:00:00Z",
    source_observed_at: "2026-07-20T10:00:05Z",
  };
  const merged = { ...base, ...overrides };
  return PAIR_DEPTH_COLUMNS.map((column) => merged[column]);
}

function order(
  partial: Partial<PairDepthOrder> & { side: "bid" | "ask"; price: number },
): PairDepthOrder {
  return {
    orderUid: "0xuid", owner: "0xowner", kind: "sell",
    amountBase: 1, amountQuote: partial.price, partiallyFillable: false,
    orderClass: "limit", creationDate: "2026-07-20T10:00:00Z",
    validTo: 1790000000, sellSymbol: "GNO", buySymbol: "WXDAI",
    ...partial,
  };
}

describe("parsePairDepth", () => {
  it("parses valid ladder rows and drops malformed ones", () => {
    const rows = parsePairDepth({
      columns: PAIR_DEPTH_COLUMNS,
      rows: [
        depthRow({ side: "ask", price: 101, amount_base: 2, amount_quote: 202 }),
        depthRow({ side: "bid", price: 99, amount_base: 1, amount_quote: 99, partially_fillable: true }),
        depthRow({ side: "mid" }), // invalid side
        depthRow({ price: null }), // missing price
        depthRow({ price: 0 }), // non-positive price
        depthRow({ price: "bad" }), // non-numeric price
        depthRow({ amount_base: 0 }), // empty remaining
        depthRow({ amount_quote: null }),
      ],
    });
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({
      side: "ask", price: 101, amountBase: 2, amountQuote: 202,
      sellSymbol: "GNO", buySymbol: "WXDAI", partiallyFillable: false,
    });
    expect(rows[1]).toMatchObject({ side: "bid", partiallyFillable: true, validTo: 1790000000 });
  });

  it("returns empty for a missing dataset", () => {
    expect(parsePairDepth(undefined)).toEqual([]);
  });
});

describe("buildDepthLadder", () => {
  it("cumulates asks best-first in BASE and keeps cum monotonic non-decreasing over ascending price", () => {
    const ladder = buildDepthLadder([
      order({ side: "ask", price: 103, amountBase: 3 }),
      order({ side: "ask", price: 101, amountBase: 1 }),
      order({ side: "ask", price: 102, amountBase: 2 }),
    ]);
    expect(ladder.asks.map((p) => p.price)).toEqual([101, 102, 103]);
    expect(ladder.asks.map((p) => p.cum)).toEqual([1, 3, 6]);
    for (let i = 1; i < ladder.asks.length; i += 1) {
      expect(ladder.asks[i].cum).toBeGreaterThanOrEqual(ladder.asks[i - 1].cum);
    }
  });

  it("cumulates bids best-first in QUOTE and emits ascending price with non-increasing cum", () => {
    const ladder = buildDepthLadder([
      order({ side: "bid", price: 100, amountQuote: 10 }),
      order({ side: "bid", price: 98, amountQuote: 30 }),
      order({ side: "bid", price: 99, amountQuote: 20 }),
    ]);
    expect(ladder.bids.map((p) => p.price)).toEqual([98, 99, 100]);
    // Best bid (100) has the smallest cum; walking down price accumulates.
    expect(ladder.bids.map((p) => p.cum)).toEqual([60, 30, 10]);
    for (let i = 1; i < ladder.bids.length; i += 1) {
      expect(ladder.bids[i].cum).toBeLessThanOrEqual(ladder.bids[i - 1].cum);
    }
  });

  it("groups same-price orders into one level with an order count", () => {
    const ladder = buildDepthLadder([
      order({ side: "ask", price: 101, amountBase: 1 }),
      order({ side: "ask", price: 101, amountBase: 2 }),
    ]);
    expect(ladder.asks).toEqual([{ price: 101, cum: 3, orders: 2 }]);
  });

  it("computes the two-sided mid", () => {
    const ladder = buildDepthLadder([
      order({ side: "bid", price: 100 }),
      order({ side: "ask", price: 101 }),
    ]);
    expect(ladder.mid).toBeCloseTo(100.5);
    expect(ladder.midKind).toBe("two_sided");
    expect(ladder.crossed).toBe(false);
  });

  it("falls back to one-sided mids", () => {
    const bidOnly = buildDepthLadder([order({ side: "bid", price: 100 }), order({ side: "bid", price: 97 })]);
    expect(bidOnly.mid).toBe(100);
    expect(bidOnly.midKind).toBe("bid_only");
    const askOnly = buildDepthLadder([order({ side: "ask", price: 101 }), order({ side: "ask", price: 104 })]);
    expect(askOnly.mid).toBe(101);
    expect(askOnly.midKind).toBe("ask_only");
  });

  it("returns a null mid for an empty book", () => {
    const ladder = buildDepthLadder([]);
    expect(ladder).toEqual({ bids: [], asks: [], mid: null, midKind: null, crossed: false });
  });

  it("flags a crossed book without erroring (legitimate on CoW)", () => {
    const ladder = buildDepthLadder([
      order({ side: "bid", price: 105 }),
      order({ side: "ask", price: 101 }),
    ]);
    expect(ladder.crossed).toBe(true);
    expect(ladder.mid).toBeCloseTo(103);
    expect(ladder.midKind).toBe("two_sided");
  });
});

describe("flipOrders", () => {
  const rows = [
    order({ side: "ask", price: 100, amountBase: 2, amountQuote: 200 }),
    order({ side: "bid", price: 80, amountBase: 1.5, amountQuote: 120 }),
  ];

  it("inverts price, swaps amounts, and swaps sides", () => {
    const flipped = flipOrders(rows);
    expect(flipped[0].side).toBe("bid");
    expect(flipped[0].price).toBeCloseTo(1 / 100);
    expect(flipped[0].amountBase).toBe(200);
    expect(flipped[0].amountQuote).toBe(2);
    expect(flipped[1].side).toBe("ask");
  });

  it("round-trips back to the original book", () => {
    const roundTripped = flipOrders(flipOrders(rows));
    expect(roundTripped).toHaveLength(rows.length);
    roundTripped.forEach((row, index) => {
      expect(row.side).toBe(rows[index].side);
      expect(row.price).toBeCloseTo(rows[index].price, 10);
      expect(row.amountBase).toBeCloseTo(rows[index].amountBase, 10);
      expect(row.amountQuote).toBeCloseTo(rows[index].amountQuote, 10);
    });
  });

  it("drops non-positive prices instead of dividing by zero", () => {
    expect(flipOrders([order({ side: "ask", price: 0 })])).toEqual([]);
  });
});

describe("countSummary", () => {
  it("summarizes both sides (asks sell BASE, bids sell QUOTE)", () => {
    const rows = [
      order({ side: "ask", price: 101 }),
      order({ side: "ask", price: 102 }),
      order({ side: "ask", price: 103 }),
      order({ side: "bid", price: 99 }),
      order({ side: "bid", price: 98 }),
    ];
    expect(countSummary(rows, "GNO", "WXDAI")).toBe("5 orders (3 sell GNO, 2 sell WXDAI)");
  });

  it("uses the singular noun and an explicit empty state", () => {
    expect(countSummary([order({ side: "ask", price: 101 })], "GNO", "WXDAI"))
      .toBe("1 order (1 sell GNO, 0 sell WXDAI)");
    expect(countSummary([], "GNO", "WXDAI")).toBe("0 orders");
  });
});

describe("pairDepthOption", () => {
  const ladder = buildDepthLadder([
    order({ side: "bid", price: 100, amountQuote: 10 }),
    order({ side: "bid", price: 99, amountQuote: 20 }),
    order({ side: "ask", price: 101, amountBase: 1 }),
    order({ side: "ask", price: 102, amountBase: 2 }),
  ]);

  it("plots asks on the left BASE axis and bids on the right QUOTE axis with [price, cum, orders] points", () => {
    const option = pairDepthOption({
      ...ladder, baseSymbol: "GNO", quoteSymbol: "WXDAI",
    });
    const series = option.series as Array<Record<string, unknown>>;
    expect(series[0]).toMatchObject({ name: "Asks", step: "end", yAxisIndex: 0 });
    expect(series[1]).toMatchObject({ name: "Bids", step: "start", yAxisIndex: 1 });
    expect(series[0].data).toEqual([[101, 1, 1], [102, 3, 1]]);
    expect(series[1].data).toEqual([[99, 30, 1], [100, 10, 1]]);
    const xAxis = option.xAxis as { name?: string; type?: string };
    expect(xAxis).toMatchObject({ type: "value", name: "WXDAI per GNO" });
    expect(JSON.stringify(option.dataZoom)).not.toContain("slider");
  });

  it("marks mid (dashed) and reference (dotted) and applies the x range", () => {
    const option = pairDepthOption({
      ...ladder, reference: 100.2, baseSymbol: "GNO", quoteSymbol: "WXDAI",
      range: { min: 95, max: 106 },
    });
    const series = option.series as Array<Record<string, unknown>>;
    const markLine = series[0].markLine as { data: Array<Record<string, unknown>> };
    expect(markLine.data).toHaveLength(2);
    expect(markLine.data[0]).toMatchObject({ xAxis: ladder.mid, lineStyle: { type: "dashed" } });
    expect(markLine.data[1]).toMatchObject({ xAxis: 100.2, lineStyle: { type: "dotted" } });
    expect(option.xAxis).toMatchObject({ min: 95, max: 106 });
  });

  it("omits the markLine entirely for an empty book with no reference", () => {
    const empty = buildDepthLadder([]);
    const option = pairDepthOption({ ...empty, baseSymbol: "GNO", quoteSymbol: "WXDAI" });
    const series = option.series as Array<Record<string, unknown>>;
    expect(series[0].markLine).toBeUndefined();
  });
});

describe("stackedSeriesOption", () => {
  const rows = [
    { bucket: "2026-07-01", chain_id: 100, fill_count: 30 },
    { bucket: "2026-07-01", chain_id: 1, fill_count: 10 },
    { bucket: "2026-07-02", chain_id: 100, fill_count: 60 },
    { bucket: "2026-07-02", chain_id: 1, fill_count: 20 },
  ];

  it("normalizes each bucket to 100% in share mode", () => {
    const option = stackedSeriesOption(rows, {
      xField: "bucket", valueField: "fill_count", seriesField: "chain_id", mode: "share",
    });
    const series = option.series as Array<{ name?: string; data: number[]; stack?: string }>;
    // Heaviest series (chain 100) stacks first.
    expect(series[0].name).toBe("100");
    expect(series[0].data).toEqual([75, 75]);
    expect(series[1].data).toEqual([25, 25]);
    expect(series.every((entry) => entry.stack === "total")).toBe(true);
    expect(option.yAxis).toMatchObject({ max: 100 });
    expect(JSON.stringify(option.dataZoom)).not.toContain("slider");
  });

  it("keeps absolute values, honors labels/colors, and stacks bars", () => {
    const option = stackedSeriesOption(rows, {
      xField: "bucket", valueField: "fill_count", seriesField: "chain_id",
      kind: "bar", seriesColors: { "100": "#34d399" },
      seriesLabeler: (name) => (name === "100" ? "Gnosis" : name),
    });
    const series = option.series as Array<Record<string, unknown>>;
    expect(series[0]).toMatchObject({ name: "Gnosis", type: "bar", itemStyle: { color: "#34d399" } });
    expect(series[0].data).toEqual([30, 60]);
  });
});

describe("growthAccountingOption", () => {
  it("stacks growth above the axis, negates churn below, and plots quick ratio on the secondary axis", () => {
    const option = growthAccountingOption([
      {
        period: "2026-06-01", active_traders: 90, new_traders: 30,
        returning_traders: 40, reactivated_traders: 20, churned_traders: 15,
        quick_ratio: 3.33, retention_rate: 0.73,
      },
    ]);
    const series = option.series as Array<Record<string, unknown>>;
    const byName = new Map(series.map((entry) => [entry.name, entry]));
    expect((byName.get("Churned") as { data: number[] }).data).toEqual([-15]);
    expect((byName.get("New") as { data: number[] }).data).toEqual([30]);
    expect(byName.get("Quick ratio")).toMatchObject({ type: "line", yAxisIndex: 1 });
    expect(JSON.stringify(option.dataZoom)).not.toContain("slider");
  });
});
