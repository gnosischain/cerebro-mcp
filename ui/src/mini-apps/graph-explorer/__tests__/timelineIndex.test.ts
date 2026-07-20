// Pure timeline machinery: bucket axis (must mirror ClickHouse bucketing),
// row dedupe, interval index, and per-frame visibility.

import { describe, expect, it } from "vitest";
import { buildGraphModel } from "../model/parseRows";
import {
  buildBucketAxis,
  buildTimelineIndex,
  computeFrame,
  dedupeTimelineEdges,
} from "../model/timelineIndex";

const A = "0xaaaa000000000000000000000000000000000001";
const B = "0xaaaa000000000000000000000000000000000002";
const C = "0xaaaa000000000000000000000000000000000003";

const NODES = [
  [A, "address", "0xaaaa…0001", []],
  [B, "address", "0xaaaa…0002", []],
  [C, "address", "0xaaaa…0003", []],
];

// TIMELINE_EDGES_COLUMNS rows:
// [id, source, target, profile, weight, edge_count, directed, bucket_start, bucket_end]
const FLOW_1 = [`t:${A}->${B}`, A, B, "t", 100, 2, true, "2026-06-01", "2026-06-01"];
const FLOW_2 = [`t:${A}->${B}`, A, B, "t", 50, 1, true, "2026-06-15", "2026-06-15"];
const STATE_1 = [`o:${A}->${C}`, A, C, "o", 1, 1, true, "2026-06-08", ""];
const STATIC_1 = [`s:${B}->${C}`, B, C, "s", 5, 1, true, "", ""];

// Weekly axis 2026-06-01 .. 2026-07-06 (6 buckets, Mondays).
const AXIS_ARGS = ["2026-06-01", "2026-07-13", "week"] as const;

describe("buildBucketAxis", () => {
  it("steps ISO-Monday weeks half-open", () => {
    const axis = buildBucketAxis(...AXIS_ARGS);
    expect(axis).toEqual([
      "2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22",
      "2026-06-29", "2026-07-06",
    ]);
  });

  it("steps calendar months", () => {
    expect(buildBucketAxis("2026-04-01", "2026-08-01", "month")).toEqual([
      "2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01",
    ]);
  });

  it("empty when range missing", () => {
    expect(buildBucketAxis("", "2026-08-01", "month")).toEqual([]);
  });
});

describe("dedupeTimelineEdges", () => {
  it("collapses per-bucket rows to one edge with summed weight", () => {
    const deduped = dedupeTimelineEdges([FLOW_1, FLOW_2, STATE_1]);
    expect(deduped).toHaveLength(2);
    const flow = deduped.find((r) => r[0] === `t:${A}->${B}`)!;
    expect(flow[4]).toBe(150); // 100 + 50 summed
    expect(flow[5]).toBe(3);
    expect(flow).toHaveLength(7); // EDGES_COLUMNS shape (parseEdgeRow-ready)
  });

  it("keeps a multi-bucket edge unknown when any bucket is unpriced", () => {
    const unpriced = [...FLOW_2] as unknown[];
    unpriced[4] = null;
    const [flow] = dedupeTimelineEdges([FLOW_1, unpriced]);
    expect(flow[4]).toBeNull();
  });
});

const SHAPES = { t: "flow", o: "state", s: "static" };

function build(rows: unknown[][]) {
  const deduped = dedupeTimelineEdges(rows);
  const model = buildGraphModel(NODES, deduped, []);
  const axis = buildBucketAxis(...AXIS_ARGS);
  const index = buildTimelineIndex(rows, model, axis, SHAPES);
  return { model, axis, index };
}

describe("buildTimelineIndex + computeFrame", () => {
  it("flow intervals are single buckets; window filters them", () => {
    const { model, index } = build([FLOW_1, FLOW_2, STATE_1, STATIC_1]);
    // Window = first bucket only: FLOW_1 (06-01) visible, FLOW_2 (06-15) not.
    const li = model.linkIds.indexOf(`t:${A}->${B}`);
    const frame0 = computeFrame(index, model, 0, 1, { showStatic: false });
    expect(frame0.linkAlpha[li]).toBeGreaterThan(0);
    const frame3 = computeFrame(index, model, 3, 1, { showStatic: false });
    expect(frame3.linkAlpha[li]).toBe(0);
    expect(frame3.linkWidth[li]).toBe(0); // hidden = alpha 0 AND width 0
  });

  it("state edges clamp open ends to the axis end", () => {
    const { model, index } = build([FLOW_1, STATE_1]);
    const li = model.linkIds.indexOf(`o:${A}->${C}`);
    // Starts bucket 1 (06-08); open end -> visible at the last bucket too.
    expect(computeFrame(index, model, 0, 1, { showStatic: false }).linkAlpha[li]).toBe(0);
    expect(
      computeFrame(index, model, 5, 1, { showStatic: false }).linkAlpha[li],
    ).toBeGreaterThan(0);
  });

  it("static rows render only when toggled on, at dim alpha, and never light nodes", () => {
    const { model, index } = build([FLOW_1, STATIC_1]);
    const li = model.linkIds.indexOf(`s:${B}->${C}`);
    const off = computeFrame(index, model, 5, 1, { showStatic: false });
    expect(off.linkAlpha[li]).toBe(0);
    const on = computeFrame(index, model, 5, 1, { showStatic: true });
    expect(on.linkAlpha[li]).toBeCloseTo(0.12);
    // C has only the static edge in this window -> stays dimmed.
    const ci = model.idToIndex.get(C)!;
    expect(on.pointAlpha[ci]).toBeLessThan(1);
  });

  it("nodes light up only with a visible incident edge", () => {
    const { model, index } = build([FLOW_1, STATE_1]);
    const frame = computeFrame(index, model, 0, 1, { showStatic: false });
    expect(frame.pointAlpha[model.idToIndex.get(A)!]).toBe(1);
    expect(frame.pointAlpha[model.idToIndex.get(B)!]).toBe(1);
    expect(frame.pointAlpha[model.idToIndex.get(C)!]).toBeLessThan(1); // state starts later
  });

  it("state edges render thin and fixed — never weight-scaled to max", () => {
    const { model, index } = build([FLOW_1, STATE_1]);
    const li = model.linkIds.indexOf(`o:${A}->${C}`);
    const frame = computeFrame(index, model, 5, 1, { showStatic: false });
    // A binary relationship: modest fixed width, dimmer than flows.
    expect(frame.linkWidth[li]).toBeLessThan(2);
    expect(frame.linkAlpha[li]).toBeLessThan(0.85);
    expect(frame.linkAlpha[li]).toBeGreaterThan(0);
  });

  it("width normalization is per profile and stable across cursors", () => {
    const { model, index } = build([FLOW_1, FLOW_2]);
    const li = model.linkIds.indexOf(`t:${A}->${B}`);
    const w0 = computeFrame(index, model, 0, 1, { showStatic: false }).linkWidth[li];
    const w2 = computeFrame(index, model, 2, 1, { showStatic: false }).linkWidth[li];
    // Bucket 0 carries the profile max (100) -> full width; bucket 2 (50) thinner.
    expect(w0).toBeGreaterThan(w2);
    expect(w2).toBeGreaterThan(0);
  });

  it("out-of-axis buckets clamp instead of dropping", () => {
    const early = [`o:${A}->${C}`, A, C, "o", 1, 1, true, "2020-01-06", ""];
    const { model, index } = build([FLOW_1, early]);
    const li = model.linkIds.indexOf(`o:${A}->${C}`);
    // Began long before the range: visible from bucket 0.
    expect(
      computeFrame(index, model, 0, 1, { showStatic: false }).linkAlpha[li],
    ).toBeGreaterThan(0);
  });
});
