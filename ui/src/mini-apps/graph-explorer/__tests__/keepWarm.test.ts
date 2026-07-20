// Sim reheat-alpha gate (pure). The camera is framed once-per-data-change by
// the tick (imperative), not by a pure gate, so it isn't unit-tested here.

import { describe, expect, it } from "vitest";
import { reEnergizeAlpha, composeLinkVisuals } from "../canvas/CosmosCanvas";

describe("reEnergizeAlpha", () => {
  it("is 0 for a no-op republish (same id set)", () => {
    expect(reEnergizeAlpha({ sameGraph: true, retained: 5, prevN: 5, nextN: 5 })).toBe(0);
  });
  it("is a full settle for a fresh / fully-churned graph", () => {
    expect(reEnergizeAlpha({ sameGraph: false, retained: 0, prevN: 0, nextN: 10 })).toBe(1);
    expect(reEnergizeAlpha({ sameGraph: false, retained: 0, prevN: 8, nextN: 10 })).toBe(1);
  });
  it("is a gentle top-up for a low-churn incremental push", () => {
    // 9 of 10 retained → churn 0.1 ≤ 0.3 → 0.3
    expect(reEnergizeAlpha({ sameGraph: false, retained: 9, prevN: 9, nextN: 10 })).toBe(0.3);
  });
});

describe("composeLinkVisuals — edge-type toggle round trip", () => {
  const model = {
    linkIds: ["a", "b"],
    linkEdgeIds: [["a", "a-parallel"], ["b"]],
    // 2 links x RGBA, both fully opaque
    linkColors: new Float32Array([1, 0, 0, 1, 0, 1, 0, 1]),
    linkWidths: new Float32Array([3, 4]),
  };

  it("applies an override (hidden link zeroed)", () => {
    const { colors, widths } = composeLinkVisuals(model, {
      alpha: new Float32Array([0, 1]),
      width: new Float32Array([0, 4]),
    });
    expect(colors[3]).toBe(0); // link 0 alpha zeroed -> hidden
    expect(colors[7]).toBe(1); // link 1 untouched
    expect(Array.from(widths)).toEqual([0, 4]);
  });

  it("RESTORES the baseline when the override is removed", () => {
    // The regression: un-hiding the last edge type drops the override to
    // undefined. Returning early left the zeroed buffers on the GPU, so edges
    // toggled off could never be toggled back on.
    const { colors, widths } = composeLinkVisuals(model, undefined);
    expect(Array.from(widths)).toEqual([3, 4]);
    expect(colors[3]).toBe(1);
    expect(colors[7]).toBe(1);
  });

  it("survives a full off -> on cycle", () => {
    const hidden = composeLinkVisuals(model, {
      alpha: new Float32Array([0, 0]),
      width: new Float32Array([0, 0]),
    });
    expect(Array.from(hidden.widths)).toEqual([0, 0]);
    const restored = composeLinkVisuals(model, undefined);
    expect(Array.from(restored.widths)).toEqual([3, 4]);
  });

  it("highlights a non-head bundled edge", () => {
    const selected = composeLinkVisuals(model, undefined, "a-parallel");
    expect(selected.colors[0]).toBe(1);
    expect(selected.colors[1]).toBeCloseTo(0.72);
    expect(selected.colors[2]).toBeCloseTo(0.12);
    expect(selected.colors[3]).toBe(1);
    expect(selected.widths[0]).toBeGreaterThan(model.linkWidths[0]);
    expect(selected.widths[1]).toBe(model.linkWidths[1]);
  });

  it("never resurrects a selected link hidden by visibility", () => {
    const selected = composeLinkVisuals(
      model,
      {
        alpha: new Float32Array([0, 1]),
        width: new Float32Array([0, 4]),
      },
      "a-parallel",
    );
    expect(selected.colors[3]).toBe(0);
    expect(selected.widths[0]).toBe(0);
  });
});
