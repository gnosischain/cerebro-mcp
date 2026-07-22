import { describe, expect, it } from "vitest";

import { candleOption, depthOption, sankeyOption, transactionExecutionGraphOption } from "../model/chartOptions";
import { buildTransactionExecutionGraph } from "../model/executionGraph";
import {
  parseCandles,
  parseCoverage,
  parseDepth,
  parseExecutionFlow,
  parseReferencePrices,
} from "../model/parseRows";

describe("CoW dataset parsers", () => {
  it("parses normalized execution candles and skips malformed/missing-decimal rows", () => {
    const rows = parseCandles({
      columns: ["bucket", "open", "high", "low", "close", "vwap", "base_volume", "quote_volume", "fill_count"],
      rows: [
        ["2026-07-20T10:00:00Z", 2, 4, 1, 3, 2.5, 10, 25, 3],
        ["2026-07-20T11:00:00Z", null, 4, 1, 3, 2.5, 10, 25, 3],
        ["2026-07-20T12:00:00Z", 2, "bad", 1, 3, 2.5, 10, 25, 3],
      ],
    });
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ open: 2, high: 4, low: 1, close: 3, vwap: 2.5 });
    const option = candleOption(rows);
    expect(option.series).toMatchObject([{ type: "candlestick" }, { name: "VWAP" }]);
    expect(JSON.stringify(option.dataZoom)).not.toContain("slider");
  });

  it("parses known-intent depth without accepting invalid sides or quantities", () => {
    const depth = parseDepth({
      columns: ["side", "limit_price", "base_quantity", "intent_count"],
      rows: [["bid", 1.2, 50, 2], ["ask", 1.3, 25, 1], ["mid", 1.25, 10, 1], ["bid", null, 10, 1]],
    });
    expect(depth).toHaveLength(2);
    expect(depthOption(depth).series).toHaveLength(2);
  });

  it("normalizes coverage warning arrays", () => {
    expect(parseCoverage({ mode: "observed_snapshot", warning_codes: "bad" })?.warning_codes).toEqual([]);
    expect(parseCoverage(null)).toBeNull();
  });

  it("keeps reference-price observations separate and skips invalid prices", () => {
    const refs = parseReferencePrices({
      columns: ["auction_timestamp", "price", "source_observed_at"],
      rows: [["2026-07-20T10:00:00Z", 1.001, "2026-07-20T10:00:02Z"], ["2026-07-20T11:00:00Z", null, "2026-07-20T11:00:02Z"]],
    });
    expect(refs).toEqual([{ bucket: "2026-07-20T10:00:00Z", price: 1.001, sourceObservedAt: "2026-07-20T10:00:02Z" }]);
  });

  it("builds a fill-count Sankey capped to twelve named nodes plus Other", () => {
    const rows = Array.from({ length: 20 }, (_, i) => [
      `0x${String(i).padStart(40, "0")}`,
      `0x${String(i + 100).padStart(40, "0")}`,
      `0x${String(i + 200).padStart(40, "0")}`,
      100 - i,
    ]);
    const links = parseExecutionFlow({ columns: ["token0", "token1", "settlement_executor", "fill_count"], rows });
    const names = new Set(links.flatMap((link) => [link.source, link.target]));
    expect(names.size).toBeLessThanOrEqual(13);
    expect(names.has("Other")).toBe(true);
    const series = sankeyOption(links).series as Array<{ type?: string }>;
    expect(series[0].type).toBe("sankey");
  });

  it("builds role-safe execution evidence without inventing solver-to-executor edges", () => {
    const graph = buildTransactionExecutionGraph({
      transaction_detail: {
        columns: ["tx_hash", "settlement_executor"],
        rows: [["0xtx", "0xactor"]],
      },
      transaction_trades: {
        columns: ["log_index", "order_uid", "sell_token", "buy_token", "sell_symbol", "buy_symbol", "sell_amount", "buy_amount"],
        rows: [[3, "0xorder", "0xsell", "0xbuy", "SELL", "BUY", 2, 4]],
      },
      transaction_interactions: {
        columns: ["log_index", "target", "selector"],
        rows: [[9, "0xtarget", "0x12345678"]],
      },
      transaction_competition: {
        columns: ["auction_id", "competition_winner", "winning_solution_solver"],
        rows: [[42, "0xactor", "0xactor"]],
      },
    });
    const ids = new Set(graph.nodes.map((node) => node.id));
    expect(ids).toContain("settlement_executor:0xactor");
    expect(ids).toContain("competition_winner:0xactor");
    expect(ids).toContain("competition_solver:0xactor");
    expect(graph.edges.some((edge) => edge.source.includes("competition_solver") && edge.target.includes("settlement_executor"))).toBe(false);
    expect(graph.edges.find((edge) => edge.relation === "winning solution")?.scope).toBe("auction_scoped");
    const option = transactionExecutionGraphOption(graph);
    const series = option.series as Array<{ type?: string; layout?: string }>;
    expect(series[0]).toMatchObject({ type: "graph", layout: "none" });
  });
});
