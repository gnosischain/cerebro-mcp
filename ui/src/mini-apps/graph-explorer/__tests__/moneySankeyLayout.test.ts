import { describe, expect, it } from "vitest";

import type { FlowEdgeRow, FlowNodeRow } from "../model/flowLayout";
import {
  buildMoneySankeyLayout,
  ZERO_ADDRESS,
} from "../model/moneySankeyLayout";

const SEED = "0x1111000000000000000000000000000000000001";
const A = "0x2222000000000000000000000000000000000002";
const B = "0x3333000000000000000000000000000000000003";
const TOKEN = "0xaaaa000000000000000000000000000000000001";

function node(id: string, rank: number, flags: string[] = []): FlowNodeRow {
  return {
    id,
    label: id === SEED ? "Seed" : id === ZERO_ADDRESS ? "Zero address" : id,
    sector: flags.includes("structural_terminal") ? "Supply" : "",
    project: "",
    hopRank: rank,
    inUsd: 0,
    outUsd: 0,
    firstSeen: "",
    lastSeen: "",
    flags,
  };
}

function edge(
  id: string,
  source: string,
  target: string,
  amountUsd: number | null = 100,
  edgeClass = "transfer",
): FlowEdgeRow {
  return {
    id,
    source,
    target,
    edgeClass,
    tokenAddress: TOKEN,
    symbol: "GNO",
    amount: 2,
    amountUsd,
    transferCount: 1,
    firstSeen: "",
    lastSeen: "",
    unknownUsdRows: amountUsd == null ? 1 : 0,
  };
}

describe("buildMoneySankeyLayout", () => {
  it("separates received and sent intermediary instances with a valueless connector", () => {
    const layout = buildMoneySankeyLayout(
      [node(SEED, 0), node(A, 1), node(B, 2)],
      [edge("e1", SEED, A, 500), edge("e2", A, B, 300)],
      [SEED],
    );

    const aNodes = layout.nodes.filter((candidate) => candidate.address === A);
    expect(aNodes.map((candidate) => candidate.role).sort()).toEqual(["received", "sent"]);
    const connector = layout.connectors.find((candidate) => candidate.address === A);
    expect(connector).toMatchObject({ kind: "analyst_expansion", value: null });
    expect(connector?.fromInstanceId).not.toBe(connector?.toInstanceId);
    expect(layout.ribbons.every((ribbon) => ribbon.id !== connector?.id)).toBe(true);
  });

  it("renders zero-address burns as terminals excluded from counterparties", () => {
    const layout = buildMoneySankeyLayout(
      [node(SEED, 0), node(ZERO_ADDRESS, 1, ["structural_terminal"])],
      [edge("burn", SEED, ZERO_ADDRESS, 700, "burn")],
      [SEED],
    );

    expect(layout.ribbons[0].eventKind).toBe("burn");
    expect(layout.nodes.find((candidate) => candidate.address === ZERO_ADDRESS)?.role).toBe(
      "terminal",
    );
    expect(layout.connectors).toHaveLength(0);
    expect(layout.hopCoverage[0]).toMatchObject({
      shownCounterparties: 0,
      loadedCounterparties: 0,
    });
  });

  it("uses a repeated hop instance when a cycle returns to the seed", () => {
    const layout = buildMoneySankeyLayout(
      [node(SEED, 0), node(A, 1)],
      [edge("out", SEED, A, 100), edge("return", A, SEED, 90)],
      [SEED],
    );

    const seedInstances = layout.nodes.filter((candidate) => candidate.address === SEED);
    expect(seedInstances.some((candidate) => candidate.role === "seed" && candidate.stage === 0)).toBe(
      true,
    );
    expect(
      seedInstances.some((candidate) => candidate.role === "received" && candidate.stage === 2),
    ).toBe(true);
  });

  it("uses one centered seed instance when incoming and outgoing lanes are both present", () => {
    const layout = buildMoneySankeyLayout(
      [node(SEED, 0), node(A, -1), node(B, 1)],
      [edge("in", A, SEED, 80), edge("out", SEED, B, 70)],
      [SEED],
    );
    const seedInstances = layout.nodes.filter(
      (candidate) => candidate.address === SEED && candidate.stage === 0,
    );
    expect(seedInstances).toHaveLength(1);
    expect(seedInstances[0].role).toBe("seed");
  });

  it("is deterministic across input permutations", () => {
    const nodes = [node(SEED, 0), node(A, 1), node(B, 1)];
    const edges = [edge("e1", SEED, A, 500), edge("e2", SEED, B, null)];
    const first = buildMoneySankeyLayout(nodes, edges, [SEED]);
    const second = buildMoneySankeyLayout(
      [nodes[2], nodes[0], nodes[1]],
      [edges[1], edges[0]],
      [SEED],
    );

    expect(second).toEqual(first);
  });

  it("keeps unpriced ribbons categorical and supports token-amount width in single-token mode", () => {
    const nodes = [node(SEED, 0), node(A, 1), node(B, 1)];
    const edges = [edge("priced", SEED, A, 500), edge("unpriced", SEED, B, null)];
    const usd = buildMoneySankeyLayout(nodes, edges, [SEED]);
    expect(usd.ribbons.find((ribbon) => ribbon.edgeIds.includes("priced"))?.widthBasis).toBe(
      "known_usd",
    );
    expect(usd.ribbons.find((ribbon) => ribbon.edgeIds.includes("unpriced"))?.widthBasis).toBe(
      "categorical",
    );

    const token = buildMoneySankeyLayout(nodes, edges, [SEED], { singleTokenMode: true });
    expect(token.ribbons.every((ribbon) => ribbon.widthBasis === "token_amount")).toBe(true);
  });

  it("caps distinct counterparties per hop without dropping supply terminals", () => {
    const nodes = [
      node(SEED, 0),
      node(A, 1),
      node(B, 1),
      node(ZERO_ADDRESS, 1, ["structural_terminal"]),
    ];
    const layout = buildMoneySankeyLayout(
      nodes,
      [
        edge("a", SEED, A, 500),
        edge("b", SEED, B, 400),
        edge("burn", SEED, ZERO_ADDRESS, 300, "burn"),
      ],
      [SEED],
      { maxCounterpartiesPerHop: 1 },
    );

    expect(layout.hopCoverage[0]).toMatchObject({
      shownCounterparties: 1,
      loadedCounterparties: 2,
      omittedCounterparties: 1,
    });
    expect(layout.ribbons.some((ribbon) => ribbon.eventKind === "burn")).toBe(true);
  });
});
