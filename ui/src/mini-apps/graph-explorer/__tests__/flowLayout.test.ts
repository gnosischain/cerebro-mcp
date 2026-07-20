// Flow layout + parsing tests. The layered layout must be deterministic
// (byte-identical across input permutations), monotone in hop_rank on x, and
// bounded within the cosmos space.

import { describe, expect, it } from "vitest";
import {
  buildFlowGraphModel,
  layeredFlowPositions,
  parseFlowEdgeRows,
  parseFlowNodeRows,
  Y_STEP_CAP,
  type FlowEdgeRow,
  type FlowNodeRow,
} from "../model/flowLayout";
import { SPACE_SIZE } from "../model/parseRows";

const TOK = "0xtok0000000000000000000000000000000000001";

function node(id: string, rank: number, inUsd = 0, outUsd = 0): FlowNodeRow {
  return {
    id,
    label: id,
    sector: "",
    project: "",
    hopRank: rank,
    inUsd,
    outUsd,
    firstSeen: "",
    lastSeen: "",
    flags: [],
  };
}
function edge(source: string, target: string): FlowEdgeRow {
  return {
    id: `flow:${source}->${target}:${TOK}`,
    source,
    target,
    edgeClass: "transfer",
    tokenAddress: TOK,
    symbol: "GNO",
    amount: 1,
    amountUsd: 100,
    transferCount: 1,
    firstSeen: "",
    lastSeen: "",
    unknownUsdRows: 0,
  };
}

describe("parseFlowNodeRows / parseFlowEdgeRows", () => {
  it("parses node rows including flags (array or python-repr string)", () => {
    const rows = [
      ["0xa", "Seed", "", "", 0, 10, 20, "t0", "t1", ["old_safe"]],
      ["0xb", "", "Privacy", "MixerX", 2, 5, 0, "", "", "['new_safe', 'refunded_safe']"],
      ["", "skip", "", "", 0, 0, 0, "", "", []],
    ];
    const parsed = parseFlowNodeRows(rows);
    expect(parsed).toHaveLength(2);
    expect(parsed[0].flags).toEqual(["old_safe"]);
    expect(parsed[1].flags).toEqual(["new_safe", "refunded_safe"]);
    expect(parsed[1].label).toBe("0xb"); // falls back to short id
    expect(parsed[1].sector).toBe("Privacy");
  });

  it("parses bridge edges with NULL USD as amountUsd null", () => {
    const rows = [
      [`bridge:0xa->0xb:${TOK}`, "0xa", "0xb", "bridge", TOK, "EURe", null, null, 4, "d0", "d1", 1],
      [`flow:0xa->0xc:${TOK}`, "0xa", "0xc", "transfer", TOK, "GNO", 2.5, 330.26, 3, "t0", "t1", 2],
    ];
    const parsed = parseFlowEdgeRows(rows);
    expect(parsed[0].amountUsd).toBeNull();
    expect(parsed[0].amount).toBeNull();
    expect(parsed[1].amountUsd).toBeCloseTo(330.26);
    expect(parsed[0].unknownUsdRows).toBe(1);
    expect(parsed[1].unknownUsdRows).toBe(2);
  });

  it("preserves unknown directional node USD instead of coercing it to zero", () => {
    const parsed = parseFlowNodeRows([
      ["0xa", "Bridge sender", "", "", 0, 0, null, "", "", []],
      ["0xb", "Bridge", "Bridges", "", 1, null, 0, "", "", []],
    ]);
    expect(parsed[0].inUsd).toBe(0);
    expect(parsed[0].outUsd).toBeNull();
    expect(parsed[1].inUsd).toBeNull();
    expect(parsed[1].outUsd).toBe(0);
  });
});

describe("layeredFlowPositions", () => {
  const nodes = [node("s", 0, 0, 300), node("a", 1, 300, 0), node("b", 1, 100, 0), node("g", -1, 0, 50)];
  const edges = [edge("g", "s"), edge("s", "a"), edge("s", "b")];

  it("is permutation-invariant (byte-identical across input orderings)", () => {
    const p1 = layeredFlowPositions(nodes, edges);
    const shuffledNodes = [nodes[2], nodes[0], nodes[3], nodes[1]];
    const shuffledEdges = [edges[2], edges[0], edges[1]];
    const p2 = layeredFlowPositions(shuffledNodes, shuffledEdges);
    for (const n of nodes) {
      expect(p2.get(n.id)).toEqual(p1.get(n.id));
    }
  });

  it("x increases monotonically with hop_rank", () => {
    const p = layeredFlowPositions(nodes, edges);
    const xG = p.get("g")!.x;
    const xS = p.get("s")!.x;
    const xA = p.get("a")!.x;
    expect(xG).toBeLessThan(xS);
    expect(xS).toBeLessThan(xA);
    // same rank → same x
    expect(p.get("a")!.x).toBe(p.get("b")!.x);
  });

  it("keeps every coordinate inside the cosmos space", () => {
    const many: FlowNodeRow[] = [];
    for (let r = -2; r <= 2; r++) {
      for (let i = 0; i < 30; i++) many.push(node(`n_${r}_${i}`, r, i, i));
    }
    const p = layeredFlowPositions(many, []);
    for (const pos of p.values()) {
      expect(pos.x).toBeGreaterThanOrEqual(0);
      expect(pos.x).toBeLessThanOrEqual(SPACE_SIZE);
      expect(pos.y).toBeGreaterThanOrEqual(0);
      expect(pos.y).toBeLessThanOrEqual(SPACE_SIZE);
    }
  });

  it("caps vertical spacing (large layers stay dense, not full-height)", () => {
    const layer: FlowNodeRow[] = [];
    for (let i = 0; i < 100; i++) layer.push(node(`n${i}`, 0, i, 0));
    const p = layeredFlowPositions(layer, []);
    const ys = layer.map((n) => p.get(n.id)!.y).sort((a, b) => a - b);
    let maxGap = 0;
    for (let i = 1; i < ys.length; i++) maxGap = Math.max(maxGap, ys[i] - ys[i - 1]);
    expect(maxGap).toBeLessThanOrEqual(Y_STEP_CAP + 1e-6);
  });

  it("centers a single-node layer", () => {
    const p = layeredFlowPositions([node("only", 0)], []);
    expect(p.get("only")!.x).toBeCloseTo(SPACE_SIZE / 2);
    expect(p.get("only")!.y).toBeCloseTo(SPACE_SIZE / 2);
  });

  it("does NOT collapse a linear chain to a flat horizontal line", () => {
    // A pure chain (every hop a single node) previously placed all nodes at
    // the vertical center → an unreadable overlapping line. The undulation
    // must give it vertical variation while keeping x monotone in rank.
    const chain = [0, 1, 2, 3, 4].map((r) => node(`c${r}`, r));
    const edges = [0, 1, 2, 3].map((r) => edge(`c${r}`, `c${r + 1}`));
    const p = layeredFlowPositions(chain, edges);
    const ys = chain.map((n) => p.get(n.id)!.y);
    const distinctYs = new Set(ys.map((y) => Math.round(y)));
    expect(distinctYs.size).toBeGreaterThan(1); // not a flat line
    // x still strictly increases with rank
    const xs = chain.map((n) => p.get(n.id)!.x);
    for (let i = 1; i < xs.length; i++) expect(xs[i]).toBeGreaterThan(xs[i - 1]);
    // still inside the space
    for (const y of ys) {
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(SPACE_SIZE);
    }
  });

  it("reduces edge crossings via barycenter (median heuristic sanity)", () => {
    // Two source layers where a straight assignment crosses; barycenter should
    // order the middle layer so connected nodes are vertically adjacent.
    const ns = [
      node("L0", 0, 5, 0),
      node("L1", 0, 4, 0),
      node("M0", 1, 3, 0),
      node("M1", 1, 2, 0),
    ];
    // L1→M0 and L0→M1 (a crossing if M0 above M1). Barycenter pulls M0 down.
    const es = [edge("L0", "M1"), edge("L1", "M0")];
    const p = layeredFlowPositions(ns, es);
    // M0 (connected to lower L1) should end up below M1 (connected to upper L0).
    expect(p.get("M0")!.y).toBeGreaterThan(p.get("M1")!.y);
  });
});

describe("buildFlowGraphModel", () => {
  it("adapts rows into a GraphModel with layered positions and sector kinds", () => {
    const nodeRows = [
      ["0xs", "Seed", "", "", 0, 0, 400, "", "", []],
      ["0xd", "CowSwap", "DEX", "CowSwap", 1, 400, 0, "", "", []],
    ];
    const edgeRows = [
      [`flow:0xs->0xd:${TOK}`, "0xs", "0xd", "transfer", TOK, "GNO", 3, 400, 2, "", ""],
    ];
    const { model, nodes, edges } = buildFlowGraphModel(nodeRows, edgeRows);
    expect(model.n).toBe(2);
    expect(model.edgeRows).toHaveLength(1);
    expect(nodes[0].id).toBe("0xs");
    expect(edges[0].edgeClass).toBe("transfer");
    // DEX node maps to the "dex" kind (palette color).
    const dexIdx = model.indexToId.indexOf("0xd");
    expect(model.nodeRows[dexIdx].kind).toBe("dex");
    // Seed (rank 0) is left of the downstream DEX (rank 1).
    const sIdx = model.indexToId.indexOf("0xs");
    expect(model.positions[sIdx * 2]).toBeLessThan(model.positions[dexIdx * 2]);
  });

  it("gives bridge edges (NULL USD) minimum weight without crashing", () => {
    const nodeRows = [
      ["0xs", "Seed", "", "", 0, 0, 0, "", "", []],
      ["0xb", "Bridge", "Bridges", "omni", 1, 0, 0, "", "", []],
    ];
    const edgeRows = [
      [`bridge:0xs->0xb:${TOK}`, "0xs", "0xb", "bridge", TOK, "EURe", null, null, 5, "", ""],
    ];
    const { model } = buildFlowGraphModel(nodeRows, edgeRows);
    expect(model.edgeRows).toHaveLength(1);
    expect(model.linkWidths.length).toBe(1);
    expect(model.linkWidths[0]).toBeGreaterThan(0); // min width, not NaN
  });
});
