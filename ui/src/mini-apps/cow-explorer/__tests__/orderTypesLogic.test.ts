// @vitest-environment jsdom
//
// Pure-logic coverage for the order-types facet: cross-chain class
// aggregation, the classified-share math behind the coverage chip (the
// 'unresolved' bucket is the denominator's honesty), and the semantic
// surplus-band ordering.

import { describe, expect, it } from "vitest";
import { classifiedShare, classSummaries, orderSurplusRows } from "../sections/OrderTypesSection";

describe("classSummaries", () => {
  it("aggregates per-chain class rows into one entry per class, orders-descending", () => {
    const summaries = classSummaries([
      { chain_id: 100, order_class: "limit", order_count: 30, owners: 5, fulfilled: 15, open_now: 2, partially_fillable_count: 8 },
      { chain_id: 1, order_class: "limit", order_count: 70, owners: 9, fulfilled: 35, open_now: 1, partially_fillable_count: 12 },
      { chain_id: 100, order_class: "market", order_count: 25, owners: 6, fulfilled: 25, open_now: 0, partially_fillable_count: 0 },
    ]);
    expect(summaries.map((s) => s.orderClass)).toEqual(["limit", "market"]);
    const limit = summaries[0];
    expect(limit.orders).toBe(100);
    expect(limit.fulfilled).toBe(50);
    expect(limit.share).toBeCloseTo(100 / 125);
    expect(limit.fillRate).toBeCloseTo(0.5);
    const market = summaries[1];
    expect(market.share).toBeCloseTo(25 / 125);
    expect(market.fillRate).toBeCloseTo(1);
  });

  it("keeps residual classes and maps empty class names to 'unknown'", () => {
    const summaries = classSummaries([
      { chain_id: 100, order_class: "", order_count: 10, owners: 1, fulfilled: 0, open_now: 0, partially_fillable_count: 0 },
      { chain_id: 100, order_class: "limit", order_count: 5, owners: 1, fulfilled: 5, open_now: 0, partially_fillable_count: 0 },
    ]);
    expect(summaries.map((s) => s.orderClass)).toEqual(["unknown", "limit"]);
  });

  it("returns an empty list (not NaN shares) for no rows", () => {
    expect(classSummaries([])).toEqual([]);
  });
});

describe("classifiedShare", () => {
  it("computes the share of orders NOT in the 'unresolved' bucket", () => {
    expect(classifiedShare([
      { order_class: "market", orders: 30 },
      { order_class: "limit", orders: 10 },
      { order_class: "twap", orders: 5 },
      { order_class: "untagged", orders: 5 }, // doc exists -> classified
      { order_class: "unresolved", orders: 50 },
    ])).toBeCloseTo(0.5);
  });

  it("sums the unresolved bucket across chains too", () => {
    expect(classifiedShare([
      { chain_id: 1, order_class: "unresolved", orders: 25 },
      { chain_id: 100, order_class: "unresolved", orders: 25 },
      { chain_id: 100, order_class: "market", orders: 50 },
    ])).toBeCloseTo(0.5);
  });

  it("returns null when there is nothing to judge from", () => {
    expect(classifiedShare([])).toBeNull();
    expect(classifiedShare([{ order_class: "market", orders: 0 }])).toBeNull();
  });
});

describe("orderSurplusRows", () => {
  it("orders bands semantically (negative -> positive, unknown last)", () => {
    const rows = orderSurplusRows([
      { surplus_bucket: "> 200 bps" },
      { surplus_bucket: "unknown" },
      { surplus_bucket: "-50-0 bps" },
      { surplus_bucket: "0-10 bps" },
      { surplus_bucket: "< -50 bps" },
    ]);
    expect(rows.map((row) => row.surplus_bucket)).toEqual([
      "< -50 bps", "-50-0 bps", "0-10 bps", "> 200 bps", "unknown",
    ]);
  });

  it("keeps unexpected band labels at the end instead of dropping them", () => {
    const rows = orderSurplusRows([
      { surplus_bucket: "brand-new-band" },
      { surplus_bucket: "0-10 bps" },
    ]);
    expect(rows.map((row) => row.surplus_bucket)).toEqual(["0-10 bps", "brand-new-band"]);
  });
});
