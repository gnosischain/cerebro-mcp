import { describe, expect, it } from "vitest";

import { quorumStatus } from "../model/quorum";

describe("quorumStatus — mirror of multiIf(quorum <= 0, unspecified, scores_total >= quorum, met, missed)", () => {
  it("met when scores_total >= quorum", () => {
    expect(quorumStatus(120000, 75000)).toEqual({ status: "met", ratio: 1.6 });
    expect(quorumStatus(75000, 75000)).toEqual({ status: "met", ratio: 1 });
  });

  it("missed when scores_total < quorum", () => {
    const result = quorumStatus(42000, 75000);
    expect(result.status).toBe("missed");
    expect(result.ratio).toBeCloseTo(0.56);
  });

  it("unspecified with null ratio when quorum is zero (nullIf semantics)", () => {
    expect(quorumStatus(5100, 0)).toEqual({ status: "unspecified", ratio: null });
  });

  it("unspecified when quorum is negative, null, undefined, or NaN", () => {
    expect(quorumStatus(100, -5).status).toBe("unspecified");
    expect(quorumStatus(100, null).status).toBe("unspecified");
    expect(quorumStatus(100, undefined).status).toBe("unspecified");
    expect(quorumStatus(100, Number.NaN).status).toBe("unspecified");
  });

  it("treats missing scores_total as zero", () => {
    expect(quorumStatus(null, 75000)).toEqual({ status: "missed", ratio: 0 });
    expect(quorumStatus(undefined, 75000).status).toBe("missed");
  });

  it("output enum never contains passed/failed vocabulary", () => {
    const statuses = new Set([
      quorumStatus(120000, 75000).status,
      quorumStatus(42000, 75000).status,
      quorumStatus(1, 0).status,
      quorumStatus(null, null).status,
    ]);
    for (const status of statuses) {
      expect(["met", "missed", "unspecified"]).toContain(status);
      expect(status).not.toMatch(/pass|fail|win/i);
    }
  });
});
