import { describe, expect, it } from "vitest";

import {
  nearestPointToClick,
  samePushTarget,
  sameNodeUniverse,
} from "../canvas/CosmosCanvas";

// The push effect SKIPS setPointPositions when it believes the renderer already
// holds this node universe. That memo lives in a ref, and a ref outlives the
// renderer it describes — so the identity of the graph instance has to be part
// of the comparison. Getting this wrong handed a freshly-created graph no point
// buffers at all, and cosmos died on the next render() with
// `(regl) missing buffer for attribute "pointIndices"`.

const G1 = { id: "graph-1" };
const G2 = { id: "graph-2" };

describe("sameNodeUniverse", () => {
  it("compares ids positionally", () => {
    expect(sameNodeUniverse(["a", "b"], ["a", "b"])).toBe(true);
    expect(sameNodeUniverse(["a", "b"], ["b", "a"])).toBe(false);
    expect(sameNodeUniverse(["a"], ["a", "b"])).toBe(false);
    expect(sameNodeUniverse([], [])).toBe(true);
  });

  it("treats a same-sized but different graph as full churn", () => {
    expect(sameNodeUniverse(["a", "b"], ["c", "d"])).toBe(false);
  });
});

describe("samePushTarget", () => {
  it("reuses positions only when the same graph got the same ids", () => {
    expect(samePushTarget({ ids: ["a", "b"], graph: G1 }, G1, ["a", "b"])).toBe(true);
  });

  it("forces a fresh push when the graph instance changed", () => {
    // The regression: identical data, new renderer. `sameNodeUniverse` alone
    // says "already pushed" and the new graph gets nothing.
    expect(sameNodeUniverse(["a", "b"], ["a", "b"])).toBe(true);
    expect(samePushTarget({ ids: ["a", "b"], graph: G1 }, G2, ["a", "b"])).toBe(false);
  });

  it("forces a fresh push on the first ever push", () => {
    expect(samePushTarget({ ids: [], graph: null }, G1, ["a"])).toBe(false);
  });

  it("still forces a fresh push when the data changed on the same graph", () => {
    expect(samePushTarget({ ids: ["a"], graph: G1 }, G1, ["a", "b"])).toBe(false);
  });

  it("does not treat an empty push as a match for an empty model", () => {
    // A fresh graph with an empty model must not be considered already-pushed
    // just because both id lists are empty.
    expect(samePushTarget({ ids: [], graph: null }, G1, [])).toBe(false);
  });
});

// Cosmos resolves a clicked point on the GPU, and on this stack that pick never
// reports a hit -- so `onPointClick` never fired and every node click fell
// through to `onBackgroundClick`, i.e. clicking a node CLEARED the selection.
// The fix picks CPU-side via cosmos's own `getPointsInRect`, which returns every
// point overlapping the pick square; this chooses among them.
describe("nearestPointToClick", () => {
  const screens: Record<number, [number, number]> = {
    3: [100, 100],
    7: [110, 104],
    9: [400, 400],
  };
  const screenOf = (i: number) => screens[i];

  it("returns -1 when the pick square caught nothing", () => {
    expect(nearestPointToClick([], screenOf, [100, 100])).toBe(-1);
  });

  it("returns the only candidate", () => {
    expect(nearestPointToClick([7], screenOf, [100, 100])).toBe(7);
  });

  it("picks the nearest centre, not the first hit", () => {
    // 3 comes first in buffer order but 7 is what the user aimed at.
    expect(nearestPointToClick([3, 7], screenOf, [111, 105])).toBe(7);
    expect(nearestPointToClick([3, 7], screenOf, [99, 99])).toBe(3);
  });

  it("is unaffected by candidate order", () => {
    expect(nearestPointToClick([7, 3], screenOf, [99, 99])).toBe(3);
    expect(nearestPointToClick([3, 7], screenOf, [99, 99])).toBe(3);
  });

  it("still resolves an exact centre hit", () => {
    expect(nearestPointToClick([3, 7, 9], screenOf, [400, 400])).toBe(9);
  });
});
