// Pure state-function tests: panel-grid reducer, chart-config normalization,
// and type-aware time-column detection. Run with `npm test` (vitest).

import { describe, expect, it } from "vitest";
import { isTimeType, timeColumn, unwrapChType } from "../catalogSearch";
import {
  MAX_CHART_PANELS,
  MAX_Y_FIELDS,
  effectiveYFields,
  normalizeChartConfig,
  type ChartPanelConfig,
  type MetricCatalogEntry,
} from "../types";
import { nextPanelId, panelsFromLegacy, panelsReducer } from "../useChartsSync";

const panel = (over: Partial<ChartPanelConfig> = {}): ChartPanelConfig => ({
  id: "c1",
  datasetKey: "primary",
  xField: "date",
  yField: "a",
  chartType: "line",
  aggregation: "sum",
  groupBy: "",
  ...over,
});

describe("normalizeChartConfig", () => {
  it("derives yFields from legacy mirrors when absent", () => {
    const c = normalizeChartConfig(panel({ yField: "a", y2Field: "b" }));
    expect(c.yFields).toEqual(["a", "b"]);
    expect(c.yField).toBe("a");
    expect(c.y2Field).toBe("b");
  });

  it("treats yFields as authoritative and re-syncs the mirrors", () => {
    const c = normalizeChartConfig(
      panel({ yField: "stale", y2Field: "old", yFields: ["x", "y", "z"] }),
    );
    expect(c.yField).toBe("x");
    expect(c.y2Field).toBe("y");
    expect(c.yFields).toEqual(["x", "y", "z"]);
  });

  it("dedupes and caps yFields", () => {
    const many = Array.from({ length: 12 }, (_, i) => `f${i % 10}`);
    const c = normalizeChartConfig(panel({ yFields: many }));
    expect(c.yFields).toHaveLength(MAX_Y_FIELDS);
    expect(new Set(c.yFields).size).toBe(MAX_Y_FIELDS);
  });

  it("round-trips: mirrors -> yFields -> mirrors", () => {
    const once = normalizeChartConfig(panel({ yField: "a", y2Field: "b" }));
    const twice = normalizeChartConfig(once);
    expect(twice).toEqual(once);
  });

  it("effectiveYFields never returns undefined", () => {
    expect(effectiveYFields(panel({ yField: "" }))).toEqual([]);
    expect(effectiveYFields(panel({ yFields: ["m1", "m2"] }))).toEqual(["m1", "m2"]);
  });
});

describe("panelsReducer", () => {
  it("adopt normalizes and never yields an empty grid", () => {
    expect(panelsReducer([], { type: "adopt", panels: [] })).toHaveLength(1);
    const adopted = panelsReducer([], {
      type: "adopt",
      panels: [panel({ yField: "a", y2Field: "b" })],
    });
    expect(adopted[0].yFields).toEqual(["a", "b"]);
  });

  it("edit of scalar mirrors takes manual control of yFields", () => {
    const state = [panel({ yFields: ["a", "b", "c"] })];
    const next = panelsReducer(state, {
      type: "edit",
      id: "c1",
      patch: { yField: "d" },
    });
    // extras beyond [0]/[1] are dropped on manual Y edits
    expect(next[0].yFields).toEqual(["d"]);
  });

  it("edit of yFields re-derives the mirrors", () => {
    const next = panelsReducer([panel()], {
      type: "edit",
      id: "c1",
      patch: { yFields: ["m1", "m2"] },
    });
    expect(next[0].yField).toBe("m1");
    expect(next[0].y2Field).toBe("m2");
  });

  it("add clones the last panel with a fresh unique id and caps at MAX", () => {
    let state = [panel()];
    state = panelsReducer(state, { type: "add" });
    expect(state).toHaveLength(2);
    expect(state[1].id).toBe("c2");
    expect(state[1].datasetKey).toBe("primary");
    for (let i = 0; i < 20; i++) {
      state = panelsReducer(state, { type: "add" });
    }
    expect(state).toHaveLength(MAX_CHART_PANELS);
    expect(new Set(state.map((p) => p.id)).size).toBe(MAX_CHART_PANELS);
  });

  it("duplicate inserts a copy right after the source", () => {
    const state = panelsReducer(
      [panel(), panel({ id: "c2", yField: "b" })],
      { type: "duplicate", id: "c1" },
    );
    expect(state.map((p) => p.id)).toEqual(["c1", "c3", "c2"]);
    expect(state[1].yField).toBe("a");
  });

  it("remove keeps at least one panel", () => {
    const two = panelsReducer(
      [panel(), panel({ id: "c2" })],
      { type: "remove", id: "c1" },
    );
    expect(two.map((p) => p.id)).toEqual(["c2"]);
    const one = panelsReducer(two, { type: "remove", id: "c2" });
    expect(one).toHaveLength(1); // last panel is not removable
  });

  it("move swaps neighbors and clamps at the edges", () => {
    const state = [panel(), panel({ id: "c2" }), panel({ id: "c3" })];
    const right = panelsReducer(state, { type: "move", id: "c1", dir: 1 });
    expect(right.map((p) => p.id)).toEqual(["c2", "c1", "c3"]);
    const clamped = panelsReducer(state, { type: "move", id: "c1", dir: -1 });
    expect(clamped.map((p) => p.id)).toEqual(["c1", "c2", "c3"]);
  });
});

describe("panel id + legacy helpers", () => {
  it("nextPanelId skips used numeric suffixes", () => {
    expect(nextPanelId([panel(), panel({ id: "c7" })])).toBe("c8");
    expect(nextPanelId([])).toBe("c1");
  });

  it("panelsFromLegacy wraps a scalar chart into one normalized panel", () => {
    const wrapped = panelsFromLegacy({
      xField: "date",
      yField: "v",
      y2Field: "w",
      chartType: "bar",
      aggregation: "avg",
      groupBy: "",
    });
    expect(wrapped).toHaveLength(1);
    expect(wrapped[0].id).toBe("c1");
    expect(wrapped[0].datasetKey).toBe("primary");
    expect(wrapped[0].yFields).toEqual(["v", "w"]);
  });
});

describe("time-column detection (frontend twin of _time_column)", () => {
  const entry = (columns: { name: string; type: string }[]): MetricCatalogEntry =>
    ({
      name: "m",
      label: "m",
      description: "",
      module: "x",
      root_model: "m",
      quality_tier: "",
      unit: "",
      allowed_dimensions: [],
      default_dimensions: [],
      columns,
    }) as MetricCatalogEntry;

  it("unwraps Nullable/LowCardinality", () => {
    expect(unwrapChType("Nullable(DateTime64(3))")).toBe("DateTime64(3)");
    expect(unwrapChType("LowCardinality(Nullable(Date))")).toBe("Date");
  });

  it("classifies CH time types", () => {
    expect(isTimeType("Date32")).toBe(true);
    expect(isTimeType("Nullable(DateTime)")).toBe(true);
    expect(isTimeType("Decimal(38, 18)")).toBe(false);
    expect(isTimeType(undefined)).toBe(false);
  });

  it("prefers typed+hinted, then typed, then untyped hint", () => {
    expect(
      timeColumn(
        entry([
          { name: "block_timestamp", type: "DateTime64(3)" },
          { name: "date", type: "Date" },
        ]),
      ),
    ).toBe("date");
    expect(
      timeColumn(entry([{ name: "created_at", type: "Nullable(DateTime)" }])),
    ).toBe("created_at");
    expect(timeColumn(entry([{ name: "date", type: "" }]))).toBe("date");
    expect(timeColumn(entry([{ name: "value", type: "UInt64" }]))).toBeNull();
  });
});
