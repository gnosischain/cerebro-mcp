import { describe, expect, it } from "vitest";
import {
  buildTxSvgGeometry,
  colorForTxToken,
  type TxSvgLeg,
  type TxSvgTransaction,
} from "../canvas/TxSvgCanvas";

const node = (id: string) => ({ id, label: id });

const leg = (
  id: string,
  source: string,
  target: string,
  logIndex: number,
  txHash = "0xselected",
): TxSvgLeg => ({
  id,
  source,
  target,
  logIndex,
  seq: logIndex,
  txHash,
  tokenAddress: `0xtoken${logIndex % 2}`,
  symbol: "TOK",
  amount: 1,
  amountUsd: null,
});

const transaction = (legs: TxSvgLeg[]): TxSvgTransaction => ({
  txHash: "0xselected",
  nodes: [...new Set(legs.flatMap((row) => [row.source, row.target]))].map(node),
  legs,
});

describe("buildTxSvgGeometry", () => {
  it("fans same-direction parallel legs into distinct symmetric arcs", () => {
    const geometry = buildTxSvgGeometry(transaction([
      leg("leg-1", "0xa", "0xb", 1),
      leg("leg-2", "0xa", "0xb", 2),
      leg("leg-3", "0xa", "0xb", 3),
    ]));
    expect(new Set(geometry.paths.map((path) => path.d)).size).toBe(3);
    expect(geometry.paths.map((path) => path.offset)).toEqual([-28, 0, 28]);
  });

  it("routes reciprocal legs on opposite canonical sides", () => {
    const geometry = buildTxSvgGeometry(transaction([
      leg("forward", "0xa", "0xb", 1),
      leg("reverse", "0xb", "0xa", 2),
    ]));
    const byId = new Map(geometry.paths.map((path) => [path.leg.id, path]));
    expect(byId.get("forward")?.offset).toBeGreaterThan(0);
    expect(byId.get("reverse")?.offset).toBeLessThan(0);
    expect(byId.get("forward")?.d).not.toBe(byId.get("reverse")?.d);
  });

  it("ends pair arcs at the node perimeter so arrowheads remain visible", () => {
    const geometry = buildTxSvgGeometry(transaction([
      leg("leg-1", "0xa", "0xb", 1),
    ]));
    const target = geometry.nodes.find((entry) => entry.node.id === "0xb");
    expect(target).toBeDefined();
    const endpoint = geometry.paths[0]?.d.match(/(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)$/);
    expect(endpoint).not.toBeNull();
    const distance = Math.hypot(
      Number(endpoint?.[1]) - (target?.x ?? 0),
      Number(endpoint?.[2]) - (target?.y ?? 0),
    );
    expect(distance).toBeCloseTo(26, 2);
  });

  it("renders repeated self-transfers as distinct deterministic loops", () => {
    const geometry = buildTxSvgGeometry(transaction([
      leg("loop-1", "0xa", "0xa", 1),
      leg("loop-2", "0xa", "0xa", 2),
      leg("loop-3", "0xa", "0xa", 3),
    ]));
    expect(geometry.paths.every((path) => path.kind === "self-loop")).toBe(true);
    expect(new Set(geometry.paths.map((path) => path.d)).size).toBe(3);
    expect(geometry.paths.map((path) => path.offset)).toEqual([42, 66, 90]);
  });

  it("is permutation-invariant and keeps stable token colours", () => {
    const legs = [
      leg("leg-3", "0xc", "0xa", 3),
      leg("leg-1", "0xa", "0xb", 1),
      leg("leg-2", "0xb", "0xa", 2),
    ];
    const a = buildTxSvgGeometry(transaction(legs));
    const bInput = transaction([...legs].reverse());
    bInput.nodes.reverse();
    const b = buildTxSvgGeometry(bInput);
    const signature = (geometry: typeof a) => geometry.paths
      .map((path) => [path.leg.id, path.d, path.color])
      .sort(([aId], [bId]) => String(aId).localeCompare(String(bId)));
    expect(signature(b)).toEqual(signature(a));
    expect(colorForTxToken("0xABC")).toBe(colorForTxToken("0xabc"));
  });

  it("uses a hub layout only when one of at least eight nodes dominates", () => {
    const star = Array.from({ length: 7 }, (_, index) =>
      leg(`leg-${index}`, "0xhub", `0xleaf${index}`, index),
    );
    const geometry = buildTxSvgGeometry(transaction(star));
    expect(geometry.layout).toBe("hub");
    expect(geometry.dominantNodeId).toBe("0xhub");
    const hub = geometry.nodes.find((entry) => entry.node.id === "0xhub");
    expect(hub).toMatchObject({ x: 0, y: 0 });
  });

  it("drops legs belonging to a different selected transaction", () => {
    const geometry = buildTxSvgGeometry(transaction([
      leg("selected", "0xa", "0xb", 1),
      leg("other", "0xb", "0xc", 2, "0xother"),
    ]));
    expect(geometry.paths.map((path) => path.leg.id)).toEqual(["selected"]);
    expect(geometry.nodes.map((entry) => entry.node.id)).toEqual(["0xa", "0xb"]);
  });

  it("preserves unknown USD as null through layout and path geometry", () => {
    const geometry = buildTxSvgGeometry(transaction([
      leg("unknown-usd", "0xa", "0xb", 1),
    ]));
    expect(geometry.paths[0]?.leg.amountUsd).toBeNull();
  });

  it("renders every leg of the audited 21-leg Gnosis receipt as a distinct path", () => {
    // 0x401d…9ca3, block 47,283,349. Independently decoded from
    // eth_getTransactionReceipt; this real settlement contains ten repeated
    // canonical pairs and many reciprocal directions around the settlement
    // hub, making it a direct fixture for the former Cosmos overlap defect.
    const hub = "0x9008d19f58aabd9ed0d60971565aa8510560ab41";
    const receiptLegs = [
      [15, "0xd48dd05ddbce6a88c66706728f2bd518e15a71d3", hub],
      [19, "0x46f2da8a69a150390a87db78e7aad8572c564963", hub],
      [20, hub, "0x46f2da8a69a150390a87db78e7aad8572c564963"],
      [23, hub, "0xd7b118271b1b7d26c9e044fc927ca31dccb22a5a"],
      [24, "0xd7b118271b1b7d26c9e044fc927ca31dccb22a5a", hub],
      [28, hub, "0x28dbd35fd79f48bfa9444d330d14683e7101d817"],
      [29, "0x28dbd35fd79f48bfa9444d330d14683e7101d817", hub],
      [33, hub, "0x5fca4cbdc182e40aefbcb91afbde7ad8d3dc18a8"],
      [34, "0x5fca4cbdc182e40aefbcb91afbde7ad8d3dc18a8", hub],
      [38, hub, "0x1865d5445010e0baf8be2eb410d3eae4a68683c2"],
      [39, "0x1865d5445010e0baf8be2eb410d3eae4a68683c2", hub],
      [43, "0xf5e40cc12f69121b0329c256a99f4ab3ebdfaa2e", hub],
      [44, hub, "0xf5e40cc12f69121b0329c256a99f4ab3ebdfaa2e"],
      [47, "0xe8a249626d3f3b876b887c30a3355513cb3fa9e4", hub],
      [48, hub, "0xe8a249626d3f3b876b887c30a3355513cb3fa9e4"],
      [51, "0x5a2fb66e66b2af7f1c2f71c6c695492faab2e587", hub],
      [52, hub, "0x5a2fb66e66b2af7f1c2f71c6c695492faab2e587"],
      [56, "0x93b7a3d164585b52f096f6eae1ec42ee267878e1", hub],
      [57, hub, "0x93b7a3d164585b52f096f6eae1ec42ee267878e1"],
      [66, hub, "0xd48dd05ddbce6a88c66706728f2bd518e15a71d3"],
      [67, hub, "0xd48dd05ddbce6a88c66706728f2bd518e15a71d3"],
    ] as const;
    const geometry = buildTxSvgGeometry(
      transaction(
        receiptLegs.map(([logIndex, source, target]) =>
          leg(`receipt-leg-${logIndex}`, source, target, logIndex),
        ),
      ),
    );

    expect(geometry.layout).toBe("hub");
    expect(geometry.dominantNodeId).toBe(hub);
    expect(geometry.paths).toHaveLength(21);
    expect(new Set(geometry.paths.map((path) => path.d))).toHaveLength(21);
    expect(geometry.paths.map((path) => path.leg.logIndex).sort((a, b) => (a ?? 0) - (b ?? 0)))
      .toEqual(receiptLegs.map(([logIndex]) => logIndex));
  });
});
