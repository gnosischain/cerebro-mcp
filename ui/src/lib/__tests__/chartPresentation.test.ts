// Contract tests for the report-surface chart presentation pass. These pin
// the behaviors the stablecoin-report regression exposed: compact `$` axis
// labels only on genuinely USD-denominated charts, date-aware ticks,
// humanized names, deterministic series order, and scatter point clouds.
// Run with `npm test` (vitest).

import { describe, expect, it } from "vitest";
import {
  applyReportPresentation,
  buildDateTicks,
  formatCompact,
  humanizeName,
} from "../chartPresentation";

type AnyRecord = Record<string, any>;

function monthly(from: number, count: number): string[] {
  const out: string[] = [];
  let y = from;
  let m = 1;
  for (let i = 0; i < count; i++) {
    out.push(`${y}-${String(m).padStart(2, "0")}-01`);
    m++;
    if (m > 12) {
      m = 1;
      y++;
    }
  }
  return out;
}

function lineSpec(overrides: AnyRecord = {}): AnyRecord {
  return {
    tooltip: { trigger: "axis" },
    legend: { data: ["USD-pegged", "non-USD"], top: 0, type: "scroll" },
    grid: { left: "3%", right: "4%", bottom: "10%", top: "40", containLabel: true },
    xAxis: { type: "category", data: monthly(2021, 40), boundaryGap: false },
    yAxis: { type: "value" },
    series: [
      {
        name: "USD-pegged",
        type: "line",
        smooth: true,
        symbolSize: 2,
        data: Array.from({ length: 40 }, (_, i) => i * 1e6),
      },
      {
        name: "non-USD",
        type: "line",
        smooth: true,
        symbolSize: 2,
        data: Array.from({ length: 40 }, (_, i) => i * 1e5),
      },
    ],
    ...overrides,
  };
}

describe("formatCompact", () => {
  it("scales with K/M/B/T and trims trailing zeros", () => {
    expect(formatCompact(1_500)).toBe("1.5K");
    expect(formatCompact(2_340_000)).toBe("2.34M");
    expect(formatCompact(150_000_000)).toBe("150M");
    expect(formatCompact(1_200_000_000)).toBe("1.2B");
  });

  it("prefixes currency and keeps the sign outside it", () => {
    expect(formatCompact(64_300_882, true)).toBe("$64.3M");
    expect(formatCompact(-1_500, true)).toBe("-$1.5K");
    expect(formatCompact(0, true)).toBe("$0");
  });
});

describe("humanizeName", () => {
  it("prettifies snake_case column names with unit suffixes", () => {
    expect(humanizeName("payment_volume_usd")).toBe("Payment volume (USD)");
    expect(humanizeName("supply_usd")).toBe("Supply (USD)");
    expect(humanizeName("active_addresses")).toBe("Active addresses");
  });

  it("capitalizes bare lowercase words and leaves real names alone", () => {
    expect(humanizeName("holders")).toBe("Holders");
    expect(humanizeName("USD-pegged")).toBe("USD-pegged");
    expect(humanizeName("sDAI")).toBe("sDAI");
    expect(humanizeName("EURe")).toBe("EURe");
  });
});

describe("buildDateTicks", () => {
  it("uses year ticks for multi-year monthly spans", () => {
    const ticks = buildDateTicks(monthly(2021, 67));
    expect(ticks).not.toBeNull();
    const labels = [...ticks!.labels.values()];
    expect(labels).toEqual(["2021", "2022", "2023", "2024", "2025", "2026"]);
    expect(ticks!.labels.get(0)).toBe("2021");
    expect(ticks!.labels.get(12)).toBe("2022");
  });

  it("returns null for non-date categories", () => {
    expect(buildDateTicks(["Bridges", "DEX", "Tokens", "Lending"])).toBeNull();
  });
});

describe("applyReportPresentation", () => {
  it("does not mutate the input spec", () => {
    const spec = lineSpec();
    const frozen = JSON.stringify(spec);
    applyReportPresentation(spec as AnyRecord, "Anything (USD)");
    expect(JSON.stringify(spec)).toBe(frozen);
  });

  it("adds $-compact axis labels when _cerebro_value_unit is usd", () => {
    const out = applyReportPresentation(
      lineSpec({ _cerebro_value_unit: "usd" }) as AnyRecord
    ) as AnyRecord;
    const fmt = out.yAxis.axisLabel.formatter;
    expect(fmt(150_000_000)).toBe("$150M");
  });

  it("infers currency from an explicit USD title", () => {
    const out = applyReportPresentation(
      lineSpec() as AnyRecord,
      "Stablecoin Supply by Peg Class (EOM, USD, excl. BRZ)"
    ) as AnyRecord;
    expect(out.yAxis.axisLabel.formatter(300_000_000)).toBe("$300M");
  });

  it("does NOT infer currency when the title counts holders/addresses", () => {
    const out = applyReportPresentation(
      lineSpec() as AnyRecord,
      "Holder Distribution by Balance Bucket, USD vs Non-USD (latest quarter)"
    ) as AnyRecord;
    expect(out.yAxis.axisLabel.formatter(180_000)).toBe("180K");
  });

  it("thins multi-year date axes to year ticks", () => {
    const out = applyReportPresentation(lineSpec() as AnyRecord) as AnyRecord;
    const axisLabel = out.xAxis.axisLabel;
    expect(typeof axisLabel.interval).toBe("function");
    expect(axisLabel.interval(0)).toBe(true);
    expect(axisLabel.interval(3)).toBe(false);
    expect(axisLabel.formatter("2021-01-01", 0)).toBe("2021");
  });

  it("sorts unstacked series (and legend) so colors follow entities", () => {
    const out = applyReportPresentation(lineSpec() as AnyRecord) as AnyRecord;
    expect(out.series.map((s: AnyRecord) => s.name)).toEqual([
      "non-USD",
      "USD-pegged",
    ]);
    expect(out.legend.data).toEqual(["non-USD", "USD-pegged"]);
  });

  it("leaves stacked series order alone", () => {
    const spec = lineSpec();
    (spec.series as AnyRecord[]).forEach((s) => (s.stack = "total"));
    const out = applyReportPresentation(spec as AnyRecord) as AnyRecord;
    expect(out.series.map((s: AnyRecord) => s.name)).toEqual([
      "USD-pegged",
      "non-USD",
    ]);
  });

  it("hides the legend for single-series cartesian charts", () => {
    const spec = lineSpec();
    spec.series = [spec.series[0]];
    spec.legend.data = ["USD-pegged"];
    const out = applyReportPresentation(spec as AnyRecord) as AnyRecord;
    expect(out.legend.show).toBe(false);
  });

  it("removes line symbols on dense series, keeps them when sparse", () => {
    const dense = applyReportPresentation(lineSpec() as AnyRecord) as AnyRecord;
    expect(dense.series[0].showSymbol).toBe(false);

    const sparse = lineSpec();
    sparse.xAxis.data = monthly(2026, 6);
    (sparse.series as AnyRecord[]).forEach((s) => (s.data = [1, 2, 3, 4, 5, 6]));
    const out = applyReportPresentation(sparse as AnyRecord) as AnyRecord;
    expect(out.series[0].symbolSize).toBe(5);
  });

  it("humanizes snake_case series names in series and legend", () => {
    const spec = lineSpec();
    spec.series = [spec.series[0]];
    spec.series[0].name = "payment_volume_usd";
    spec.legend.data = ["payment_volume_usd"];
    const out = applyReportPresentation(spec as AnyRecord) as AnyRecord;
    expect(out.series[0].name).toBe("Payment volume (USD)");
  });

  it("turns one-point-per-series scatters into labeled log point clouds", () => {
    const tokens: [string, number, number][] = [
      ["WxDAI", 49_175, 64_300_882],
      ["sDAI", 28_578, 58_883_818],
      ["EURe", 39_914, 22_846_437],
      ["ZCHF", 286, 1_699_456],
      ["GBPe", 1_299, 480_218],
      ["BRZ", 176, 41_375_952],
    ];
    const spec: AnyRecord = {
      tooltip: { trigger: "item" },
      legend: { data: tokens.map((t) => t[0]), top: 0, type: "scroll" },
      grid: { left: "3%", right: "4%", bottom: "10%", top: "40", containLabel: true },
      xAxis: { type: "value", name: "holders" },
      yAxis: { type: "value", name: "supply_usd" },
      series: tokens.map(([name, x, y]) => ({
        name,
        type: "scatter",
        data: [[x, y]],
        symbolSize: 6,
      })),
    };
    const out = applyReportPresentation(spec) as AnyRecord;
    expect(out.legend.show).toBe(false);
    expect(out.series[0].label.show).toBe(true);
    expect(out.series[0].labelLayout).toEqual({ hideOverlap: true });
    // holders spread 176..49K and supply 1.7M..64M both exceed 50x -> log
    expect(out.xAxis.type).toBe("log");
    expect(out.yAxis.type).toBe("log");
    expect(out.xAxis.name).toBe("Holders");
    expect(out.yAxis.name).toBe("Supply (USD)");
    // supply axis is $-formatted from its raw name, holders axis is not
    expect(out.yAxis.axisLabel.formatter(10_000_000)).toBe("$10M");
    expect(out.xAxis.axisLabel.formatter(10_000)).toBe("10K");
    expect(out.grid.right).toBe(64);
  });

  it("strips numeric ordering prefixes from bucket categories", () => {
    const spec = lineSpec();
    spec.series = [
      { name: "holders", type: "bar", data: [1, 2, 3, 4] },
    ];
    spec.legend.data = ["holders"];
    spec.xAxis = {
      type: "category",
      data: ["1 dust ≤$1", "2 $1-10", "3 $10-100", "4 $100-1k"],
    };
    const out = applyReportPresentation(spec as AnyRecord) as AnyRecord;
    expect(out.xAxis.axisLabel.interval).toBe(0);
    expect(out.xAxis.axisLabel.formatter("1 dust ≤$1")).toBe("dust ≤$1");
  });

  it("never overrides an explicit axis formatter", () => {
    const spec = lineSpec({ _cerebro_value_unit: "usd" });
    spec.yAxis = { type: "value", axisLabel: { formatter: "{value} gwei" } };
    const out = applyReportPresentation(spec as AnyRecord) as AnyRecord;
    expect(out.yAxis.axisLabel.formatter).toBe("{value} gwei");
  });
});
