import { describe, expect, it } from "vitest";
import {
  RING_MAX_RADIUS,
  RING_MIN_RADIUS,
  buildTxGraphModel,
  groupLegsByTx,
  parseTxLegRows,
  parseTxListRows,
  parseTxNodeRows,
  txRingPositions,
  txNodeKind,
} from "../model/txLayout";
import { SPACE_SIZE } from "../model/parseRows";

// Columns: id,label,role,project,column_rank,in_usd,out_usd,leg_count,flags
const node = (id: string, role: string, flags: string[] = []) => [
  id, id.slice(0, 8), role, "", 0, 0, 0, 1, flags,
];
// Columns: id,source,target,tx_hash,log_index,block_number,transaction_index,
//          block_timestamp,token_address,symbol,amount,amount_usd,seq,tx_rank,
//          tx_status,raw_amount
const leg = (
  seq: number,
  src: string,
  tgt: string,
  logIndex: number,
  symbol = "EURe",
  txHash = "0xtx1",
) => [
  `leg:${txHash}:${logIndex}`, src, tgt, txHash, logIndex, 100, 1,
  "2026-07-02T01:18:05", "0xtoken", symbol, 1, 10, seq, 0, "success", "1000000",
];

describe("parseTxLegRows", () => {
  it("preserves receipt status, exact raw amount, and nullable USD", () => {
    const row = leg(0, "0xa", "0xb", 3) as unknown[];
    row[10] = null;
    row[11] = null;
    const [parsed] = parseTxLegRows([row]);
    expect(parsed).toMatchObject({
      txStatus: "success",
      rawAmount: "1000000",
      amount: null,
      amountUsd: null,
    });
  });
});

describe("parseTxNodeRows", () => {
  it("preserves unknown directional USD instead of coercing it to zero", () => {
    const row = node("0xa", "address") as unknown[];
    row[5] = null;
    row[6] = 0;
    const [parsed] = parseTxNodeRows([row]);
    expect(parsed.inUsd).toBeNull();
    expect(parsed.outUsd).toBe(0);
  });
});

describe("parseTxListRows", () => {
  it("deduplicates and orders address-discovery results newest first", () => {
    expect(
      parseTxListRows([
        ["0xold", 100, 2, "2026-07-01", 3, 1],
        ["0xnew", 110, 1, "2026-07-02", 5, 2],
        ["0xold", 100, 1, "2026-07-01", 2, 1],
      ]).map((row) => row.txHash),
    ).toEqual(["0xnew", "0xold"]);
  });
});

describe("txNodeKind", () => {
  it("separates token contracts and burn addresses from counterparties", () => {
    // A leg ending at an ERC-20 contract or the zero address is a
    // mint/burn/reserve payout, NOT a payment to someone. Conflating the two
    // invalidated an earlier investigation, so the kinds must stay distinct.
    expect(txNodeKind({ role: "token", flags: [] } as never)).toBe("token");
    expect(txNodeKind({ role: "burn", flags: [] } as never)).toBe("burn");
    expect(txNodeKind({ role: "address", flags: ["token_contract"] } as never)).toBe("token");
    expect(txNodeKind({ role: "address", flags: [] } as never)).toBe("address");
  });
});

describe("txRingPositions", () => {
  const nodes = [
    node("0xa", "address"),
    node("0xb", "address"),
    node("0xc", "address"),
    node("0xd", "address"),
    node("0xe", "address"),
  ].map((r) => ({
    id: String(r[0]), label: String(r[1]), role: String(r[2]), project: "",
    columnRank: 0, inUsd: 0, outUsd: 0, legCount: 1, flags: r[8] as string[],
  }));
  const legs = parseTxLegRows([
    leg(0, "0xa", "0xb", 1),
    leg(1, "0xb", "0xc", 2),
    leg(2, "0xc", "0xd", 3),
    leg(3, "0xd", "0xe", 4),
  ]);

  it("keeps a small transaction inside one screenful", () => {
    // Regression: an uncapped step spread 5 nodes across the full 8192-unit
    // space (~1638 apart), so an auto-fit framed one node and the rest sat
    // off-screen — the canvas looked empty.
    const pos = txRingPositions(nodes, legs);
    const xs = [...pos.values()].map((p) => p.x);
    const span = Math.max(...xs) - Math.min(...xs);
    expect(span).toBeLessThanOrEqual(RING_MAX_RADIUS * 2);
    expect(span).toBeLessThan(SPACE_SIZE / 2);
  });

  it("centers the ring in the coordinate space", () => {
    // CENTROID, not bounding box: a regular polygon with an odd vertex count
    // has an asymmetric bbox (one vertex at the top, two low), so a bbox-centre
    // assertion fails on a perfectly centred ring.
    const pos = txRingPositions(nodes, legs);
    const pts = [...pos.values()];
    const cx = pts.reduce((s, p) => s + p.x, 0) / pts.length;
    const cy = pts.reduce((s, p) => s + p.y, 0) / pts.length;
    expect(Math.abs(cx - SPACE_SIZE / 2)).toBeLessThan(1);
    expect(Math.abs(cy - SPACE_SIZE / 2)).toBeLessThan(1);
    // Every participant sits on the ring — equidistant from the centre.
    const radii = pts.map((p) => Math.hypot(p.x - cx, p.y - cy));
    expect(Math.max(...radii) - Math.min(...radii)).toBeLessThan(1);
  });

  it("spreads participants in 2D so edges are not collinear", () => {
    // The column layout put everyone on a near-horizontal line, so edges
    // between DIFFERENT pairs ran along a shared axis and piled up. Curvature
    // cannot fix that — the curve is an offset on the line's own normal.
    const pos = txRingPositions(nodes, legs);
    const ys = [...pos.values()].map((p) => p.y);
    const ySpread = Math.max(...ys) - Math.min(...ys);
    const xs = [...pos.values()].map((p) => p.x);
    const xSpread = Math.max(...xs) - Math.min(...xs);
    // A ring spreads comparably on both axes; a line does not.
    expect(ySpread).toBeGreaterThan(xSpread * 0.5);
    expect(ySpread).toBeGreaterThanOrEqual(RING_MIN_RADIUS);
  });

  it("is permutation-invariant — the same tx always draws the same way", () => {
    const a = txRingPositions(nodes, legs);
    const b = txRingPositions([...nodes].reverse(), [...legs].reverse());
    for (const [id, p] of a) {
      expect(b.get(id)!.x).toBeCloseTo(p.x, 6);
      expect(b.get(id)!.y).toBeCloseTo(p.y, 6);
    }
  });

  it("pushes token contracts and burn addresses to the outer lanes", () => {
    const mixed = [
      { ...nodes[0], id: "0xtok", role: "token", flags: ["token_contract"] },
      { ...nodes[1], id: "0xmid", role: "address", flags: [] },
      { ...nodes[2], id: "0xburn", role: "burn", flags: ["burn_address"] },
    ];
    const mixedLegs = parseTxLegRows([
      leg(0, "0xtok", "0xmid", 1),
      leg(1, "0xmid", "0xburn", 2),
    ]);
    const pos = txRingPositions(mixed, mixedLegs);
    // Lane order is preserved as ANGULAR order around the ring: token first
    // (top), then the actor, then burn — so a mint never reads as a hop.
    const ang = (id: string) => {
      const p = pos.get(id)!;
      return Math.atan2(p.y - SPACE_SIZE / 2, p.x - SPACE_SIZE / 2);
    };
    expect(ang("0xtok")).toBeLessThan(ang("0xmid"));
    expect(ang("0xmid")).toBeLessThan(ang("0xburn"));
  });
});

describe("groupLegsByTx", () => {
  it("groups by transaction and preserves chain order within each", () => {
    const legs = parseTxLegRows([
      leg(2, "0xc", "0xd", 9, "EURe", "0xtx2"),
      leg(0, "0xa", "0xb", 1, "EURe", "0xtx1"),
      leg(1, "0xb", "0xc", 5, "GNO", "0xtx1"),
    ]);
    const groups = groupLegsByTx(legs);
    expect(groups.map((g) => g.txHash)).toEqual(["0xtx1", "0xtx2"]);
    expect(groups[0].legs.map((l) => l.logIndex)).toEqual([1, 5]);
    expect(groups[0].tokens).toEqual(["EURe", "GNO"]);
  });
});

describe("buildTxGraphModel", () => {
  it("emits ONE canvas edge per leg — repeated pairs stay separate", () => {
    // Aggregating (src,tgt) would hide the very thing this mode exists to
    // show: the same pair transacting twice at different log indices.
    const rows = [
      leg(0, "0xa", "0xb", 1),
      leg(1, "0xa", "0xb", 2, "GNO"),
    ];
    const { model, legs } = buildTxGraphModel(
      [node("0xa", "address"), node("0xb", "address")] as unknown[][],
      rows as unknown[][],
    );
    expect(legs).toHaveLength(2);
    expect(model.edgeRows).toHaveLength(2);
    expect(model.n).toBe(2);
  });

  it("keeps an unpriced leg non-numeric in the generic fallback model", () => {
    const unpriced = leg(0, "0xa", "0xb", 1) as unknown[];
    unpriced[11] = null;
    const { model } = buildTxGraphModel(
      [node("0xa", "address"), node("0xb", "address")] as unknown[][],
      [unpriced],
    );
    expect(Number.isNaN(model.edgeRows[0]?.weight)).toBe(true);
  });
});
