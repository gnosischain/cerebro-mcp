// parseRows tests: malformed rows are skipped, dangling edges are dropped,
// and the active-profile filter (with its widen safety net) works.

import { describe, expect, it } from "vitest";
import {
  buildGraphModel,
  filterEvidenceRows,
  parseEdgeRows,
  parseEvidenceRows,
  parseNodeRows,
} from "../model/parseRows";
import { colorForRelationship, PROFILE_PALETTE } from "../model/palette";

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

  it("does not coerce an unknown edge weight to a forensic zero", () => {
    const [edge] = parseEdgeRows([
      ["e-unknown", "0xa", "0xb", "token_transfers", null, 1, true],
    ]);
    expect(Number.isNaN(edge.weight)).toBe(true);
  });
});

describe("evidence attribution", () => {
  it("rejects legacy/unattributed rows and parses subject + request identity", () => {
    expect(parseEvidenceRows([["0xa", "balance", "10"]])).toEqual([]);
    expect(
      parseEvidenceRows([
        ["0xa", "balance", "10", "node", 7],
        ["e1", "weight", "3", "edge", 8],
        ["0xb", "bad", "row", "unknown", 9],
      ]),
    ).toEqual([
      {
        ownerId: "0xa",
        column: "balance",
        value: "10",
        subjectKind: "node",
        requestId: 7,
      },
      {
        ownerId: "e1",
        column: "weight",
        value: "3",
        subjectKind: "edge",
        requestId: 8,
      },
    ]);
  });

  it("never renders late A evidence after client intent advanced to B", () => {
    const rows = parseEvidenceRows([
      ["0xB", "label", "fresh B", "node", 2],
      ["0xA", "label", "late A", "node", 1],
      ["0xB", "label", "old B", "node", 1],
      ["0xB", "weight", "wrong kind", "edge", 2],
    ]);
    expect(
      filterEvidenceRows(rows, {
        subjectKind: "node",
        subjectId: "0xB",
        requestId: 2,
      }).map((row) => row.value),
    ).toEqual(["fresh B"]);
    expect(filterEvidenceRows(rows, null)).toEqual([]);
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

  it("treats an unresolved empty profile list as not-yet-synced", () => {
    const model = buildGraphModel(NODES, EDGES, [], {
      profileSelectionPhase: "unresolved",
    });
    expect(model.edgeRows.map((edge) => edge.id)).toEqual(["e1", "e2"]);
    expect(model.nodeRows.map((node) => node.id)).toEqual(["0xa", "0xb", "0xc"]);
  });

  it("treats an applied empty profile list as an instruction with no orphans", () => {
    const model = buildGraphModel(NODES, EDGES, [], {
      profileSelectionPhase: "applied",
    });
    expect(model.edgeRows).toEqual([]);
    expect(model.nodeRows).toEqual([]);
    expect(model.n).toBe(0);
  });

  it("does not widen an applied selection that has no matching edges", () => {
    const model = buildGraphModel(NODES, EDGES, ["missing"], {
      profileSelectionPhase: "applied",
    });
    expect(model.edgeRows).toEqual([]);
    expect(model.nodeRows).toEqual([]);
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

  it("keeps relationship colors stable across edge permutations and reloads", () => {
    const forward = buildGraphModel(NODES, EDGES, []);
    const reversed = buildGraphModel(NODES, [...EDGES].reverse(), []);
    const reloaded = buildGraphModel(NODES, EDGES.map((row) => [...row]), []);

    for (const profile of ["p1", "p2"]) {
      const expected = colorForRelationship(profile);
      expect(PROFILE_PALETTE).toContain(expected);
      expect(forward.profileColor.get(profile)).toBe(expected);
      expect(reversed.profileColor.get(profile)).toBe(expected);
      expect(reloaded.profileColor.get(profile)).toBe(expected);
    }
  });

  it("uses categorical widths when relationship units are mixed", () => {
    const model = buildGraphModel(
      NODES,
      [
        ["usd", "0xa", "0xb", "usd_profile", 1_000_000, 1, true],
        ["ownership", "0xb", "0xc", "ownership_profile", 1, 1, true],
      ],
      [],
    );
    expect(Array.from(model.linkWidths)).toEqual([1.5, 1.5]);
  });

  it("retains quantitative widths within one relationship unit", () => {
    const model = buildGraphModel(
      NODES,
      [
        ["small", "0xa", "0xb", "usd_profile", 10, 1, true],
        ["large", "0xb", "0xc", "usd_profile", 10_000, 1, true],
      ],
      [],
    );
    expect(model.linkWidths[1]).toBeGreaterThan(model.linkWidths[0]);
  });
});

describe("parallel link bundling", () => {
  const nodes = [["a", "address", "A", []], ["b", "address", "B", []]];
  // Three SAME-direction edges a→b, plus one reciprocal b→a.
  const edges = [
    ["e1", "a", "b", "p1", 10, 1, true],
    ["e2", "a", "b", "p1", 20, 1, true],
    ["e3", "a", "b", "p2", 30, 1, true],
    ["e4", "b", "a", "p1", 40, 1, true],
  ];

  it("does NOT bundle by default — Timeline depends on 1 link per edge row", () => {
    // Timeline's edgeRows hold one row per (pair, time BUCKET). Collapsing on
    // (source,target) there would fuse every bucket into a single link and
    // destroy playback, so bundling must stay opt-in.
    const m = buildGraphModel(nodes, edges, []);
    expect(m.linkIds).toHaveLength(4);
    expect(m.linkCounts).toEqual([1, 1, 1, 1]);
  });

  it("bundles same-direction parallels, keeps the reciprocal separate", () => {
    // Cosmos curves a→b and b→a to opposite sides so they are both visible,
    // but three a→b edges draw the IDENTICAL arc and stack invisibly.
    const m = buildGraphModel(nodes, edges, [], { collapseParallel: true });
    expect(m.linkIds).toHaveLength(2); // a→b bundle + b→a
    expect(m.linkCounts).toEqual([3, 1]);
    expect(m.linkEdgeIds).toEqual([["e1", "e2", "e3"], ["e4"]]);
    // Every edge remains available for selection / evidence.
    expect(m.edgeRows).toHaveLength(4);
    // The bundle keeps a REAL edge id so selecting it opens genuine evidence.
    expect(m.edgeRows.some((e) => e.id === m.linkIds[0])).toBe(true);
  });

  it("carries every profile in a bundle, so hiding one type cannot hide it all", () => {
    const m = buildGraphModel(nodes, edges, [], { collapseParallel: true });
    expect(new Set(m.linkProfiles[0])).toEqual(new Set(["p1", "p2"]));
  });

  it("draws a bundle thicker than a single edge, but sub-linearly", () => {
    const m = buildGraphModel(nodes, edges, [], { collapseParallel: true });
    const [bundle, lone] = [m.linkWidths[0], m.linkWidths[1]];
    expect(bundle).toBeGreaterThan(lone);
    // 3 edges must not render 3x thicker — dust would out-shout real value.
    expect(bundle).toBeLessThan(lone * 3);
  });

  it("counts degree over every edge, not every bundle", () => {
    const m = buildGraphModel(nodes, edges, [], { collapseParallel: true });
    expect(m.degrees[0]).toBe(4); // node A touches all four edges
    expect(m.degrees[1]).toBe(4);
  });
});
