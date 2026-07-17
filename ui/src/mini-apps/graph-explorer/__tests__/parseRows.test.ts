// parseRows tests: malformed rows are skipped, dangling edges are dropped,
// and the active-profile filter (with its widen safety net) works.

import { describe, expect, it } from "vitest";
import {
  buildGraphModel,
  parseEdgeRows,
  parseNodeRows,
} from "../model/parseRows";

const NODES: unknown[][] = [
  ["0xa", "address", "A", ["p1"]],
  ["0xb", "safe", "B", ["p1", "p2"]],
  ["0xc", "token", "C", []],
];

const EDGES: unknown[][] = [
  ["e1", "0xa", "0xb", "p1", 10, 2, true],
  ["e2", "0xb", "0xc", "p2", 5, 1, false],
];

describe("parseNodeRows / parseEdgeRows", () => {
  it("skips malformed rows", () => {
    const nodes = parseNodeRows([
      ...NODES,
      ["", "address", "empty id", []],
      [null, "address", "null id", []],
      "not-a-row" as unknown as unknown[],
    ]);
    expect(nodes.map((n) => n.id)).toEqual(["0xa", "0xb", "0xc"]);

    const edges = parseEdgeRows([
      ...EDGES,
      ["e3", "", "0xc", "p1", 1, 1, true], // missing source
      ["", "0xa", "0xc", "p1", 1, 1, true], // missing id
      42 as unknown as unknown[],
    ]);
    expect(edges.map((e) => e.id)).toEqual(["e1", "e2"]);
  });

  it("fills defaults for sparse rows", () => {
    const [n] = parseNodeRows([["0xz"]]);
    expect(n).toEqual({ id: "0xz", kind: "address", label: "", profiles: [] });
  });
});

describe("buildGraphModel", () => {
  it("drops dangling edges (endpoint not in the node set)", () => {
    const model = buildGraphModel(
      NODES,
      [...EDGES, ["e9", "0xa", "0xMISSING", "p1", 1, 1, true]],
      [],
    );
    expect(model.linkIds).toEqual(["e1", "e2"]);
    expect(model.edgeRows.map((e) => e.id)).toEqual(["e1", "e2"]);
    // links carries index pairs for exactly the surviving edges
    expect(model.links.length).toBe(4);
  });

  it("filters edges by active profile", () => {
    const model = buildGraphModel(NODES, EDGES, ["p2"]);
    expect(model.linkIds).toEqual(["e2"]);
    expect(model.linkArrows).toEqual([false]);
  });

  it("keeps all edges when the filter would blank a non-empty graph (widen safety net)", () => {
    const model = buildGraphModel(NODES, EDGES, ["profile_with_no_edges"]);
    expect(model.linkIds).toEqual(["e1", "e2"]);
  });

  it("computes degrees and index maps", () => {
    const model = buildGraphModel(NODES, EDGES, []);
    expect(model.n).toBe(3);
    expect(model.idToIndex.get("0xb")).toBe(1);
    expect(model.indexToId[2]).toBe("0xc");
    expect(Array.from(model.degrees)).toEqual([1, 2, 1]);
    expect(model.profileColor.has("p1")).toBe(true);
    expect(model.profileColor.has("p2")).toBe(true);
  });
});
