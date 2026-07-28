import { describe, it, expect } from "vitest";
import {
  buildHistoryOption,
  isChartable,
  type HistorySeries,
} from "../historyChartOption";

function series(overrides: Partial<HistorySeries> = {}): HistorySeries {
  return {
    signature: "totalSupply()",
    range_label: "30d",
    from_block: 100,
    to_block: 400,
    output_index: 0,
    decimals: 18,
    output_types: ["uint256"],
    points: [
      { block: 100, timestamp: 1_700_000_000, status: "ok", value: "1", value_float: 1, error: "" },
      { block: 200, timestamp: 1_700_086_400, status: "ok", value: "2", value_float: 2, error: "" },
      { block: 300, timestamp: 1_700_172_800, status: "ok", value: "3", value_float: 3, error: "" },
    ],
    ok_count: 3,
    truncated: false,
    warnings: [],
    swept_at: "2026-07-27T00:00:00Z",
    ...overrides,
  };
}

describe("buildHistoryOption", () => {
  it("uses inside-only zoom — slider bars are banned on every chart surface", () => {
    const option = buildHistoryOption(series(), true);
    expect(JSON.stringify(option.dataZoom)).not.toContain("slider");
    expect(JSON.stringify(option.dataZoom)).toContain("inside");
  });

  it("plots timestamps in milliseconds on a time axis", () => {
    const option = buildHistoryOption(series(), false);
    const data = (option.series as any[])[0].data as [number, number][];
    expect((option.xAxis as any).type).toBe("time");
    expect(data[0][0]).toBe(1_700_000_000 * 1000);
  });

  it("renders failed samples as gaps, never as zero", () => {
    const withFailure = series({
      points: [
        { block: 100, timestamp: 1_700_000_000, status: "ok", value: "1", value_float: 1, error: "" },
        { block: 200, timestamp: 1_700_086_400, status: "reverted", value: null, value_float: null, error: "execution reverted" },
        { block: 300, timestamp: 1_700_172_800, status: "ok", value: "3", value_float: 3, error: "" },
      ],
      ok_count: 2,
    });
    const option = buildHistoryOption(withFailure, false);
    const data = (option.series as any[])[0].data as [number, number | null][];
    expect(data[1][1]).toBeNull();
    expect((option.series as any[])[0].connectNulls).toBe(false);
  });

  it("keeps the y-axis scaled rather than zero-anchored", () => {
    // A supply that moves 1% would look flat against a zero baseline.
    const option = buildHistoryOption(series(), false);
    expect((option.yAxis as any).scale).toBe(true);
  });

  it("hides point symbols on dense series", () => {
    const dense = series({
      points: Array.from({ length: 120 }, (_, i) => ({
        block: 100 + i,
        timestamp: 1_700_000_000 + i * 100,
        status: "ok",
        value: String(i),
        value_float: i,
        error: "",
      })),
      ok_count: 120,
    });
    expect((buildHistoryOption(dense, false).series as any[])[0].showSymbol).toBe(false);
    expect((buildHistoryOption(series(), false).series as any[])[0].showSymbol).toBe(true);
  });
});

describe("isChartable", () => {
  it("accepts a series with at least one numeric sample", () => {
    expect(isChartable(series())).toBe(true);
  });

  it("rejects a series where every sample failed", () => {
    expect(
      isChartable(
        series({
          points: [
            { block: 100, timestamp: 1, status: "not_deployed", value: null, value_float: null, error: "no code" },
          ],
          ok_count: 0,
        }),
      ),
    ).toBe(false);
  });

  it("rejects non-numeric returns such as address or string", () => {
    expect(
      isChartable(
        series({
          output_types: ["address"],
          points: [
            { block: 100, timestamp: 1, status: "ok", value: "0xabc", value_float: null, error: "" },
          ],
        }),
      ),
    ).toBe(false);
  });
});
