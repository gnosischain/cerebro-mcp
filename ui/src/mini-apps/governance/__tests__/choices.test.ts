import { describe, expect, it } from "vitest";

import { leadingChoice, pairChoices, renderVoteChoice } from "../model/choices";

const CHOICES = ["For", "Against", "Abstain"];

describe("pairChoices", () => {
  it("zips choices with scores, 1-based", () => {
    const paired = pairChoices('["For","Against","Abstain"]', "[100, 40, 5]");
    expect(paired.mismatch).toBe(false);
    expect(paired.entries).toEqual([
      { index: 1, label: "For", score: 100 },
      { index: 2, label: "Against", score: 40 },
      { index: 3, label: "Abstain", score: 5 },
    ]);
  });

  it("flags a length mismatch (pending scores) without dropping choices", () => {
    const paired = pairChoices('["For","Against"]', "[100]");
    expect(paired.mismatch).toBe(true);
    expect(paired.entries).toHaveLength(2);
    expect(paired.entries[1].score).toBeNull();
  });

  it("flags missing/malformed scores JSON as mismatch, entries keep null scores", () => {
    const paired = pairChoices('["For","Against"]', "not-json");
    expect(paired.mismatch).toBe(true);
    expect(paired.entries.map((e) => e.score)).toEqual([null, null]);
  });

  it("malformed choices JSON yields empty entries + mismatch", () => {
    expect(pairChoices("{oops", "[1]")).toEqual({ entries: [], mismatch: true });
  });
});

describe("leadingChoice — signaling vocabulary only", () => {
  it("picks the max score with its share", () => {
    const leading = leadingChoice(pairChoices(JSON.stringify(CHOICES), "[100, 40, 60]").entries);
    expect(leading).toMatchObject({ index: 1, label: "For", score: 100, tie: false });
    expect(leading!.share).toBeCloseTo(0.5);
  });

  it("marks ties", () => {
    const leading = leadingChoice(pairChoices(JSON.stringify(CHOICES), "[100, 100, 60]").entries);
    expect(leading!.tie).toBe(true);
  });

  it("returns null on empty entries and on all-zero scores (nothing is leading)", () => {
    expect(leadingChoice([])).toBeNull();
    expect(leadingChoice(pairChoices(JSON.stringify(CHOICES), "[0, 0, 0]").entries)).toBeNull();
    expect(leadingChoice(pairChoices(JSON.stringify(CHOICES), "null").entries)).toBeNull();
  });

  it("never speaks in passed/failed vocabulary", () => {
    const leading = leadingChoice(pairChoices(JSON.stringify(CHOICES), "[100, 40, 5]").entries);
    const text = JSON.stringify(leading).toLowerCase();
    expect(text).not.toContain("passed");
    expect(text).not.toContain("failed");
    expect(text).not.toContain("winner");
  });
});

describe("renderVoteChoice", () => {
  it("renders a 1-based int as the single choice label", () => {
    expect(renderVoteChoice(1, CHOICES)).toMatchObject({ kind: "single", text: "For", outOfRange: false });
    expect(renderVoteChoice("2", CHOICES)).toMatchObject({ kind: "single", text: "Against" });
  });

  it("renders a ranked array as ordered preferences", () => {
    const rendered = renderVoteChoice([2, 1, 3], CHOICES);
    expect(rendered.kind).toBe("ranked");
    expect(rendered.parts).toEqual(["1st: Against", "2nd: For", "3rd: Abstain"]);
    expect(rendered.outOfRange).toBe(false);
  });

  it("parses JSON-encoded ranked arrays (raw wire shape)", () => {
    expect(renderVoteChoice("[3,1]", CHOICES).parts).toEqual(["1st: Abstain", "2nd: For"]);
  });

  it("flags out-of-range indexes instead of guessing", () => {
    expect(renderVoteChoice(9, CHOICES)).toMatchObject({ kind: "single", outOfRange: true });
    expect(renderVoteChoice([1, 7], CHOICES).outOfRange).toBe(true);
  });

  it("objects and malformed values render as unknown", () => {
    expect(renderVoteChoice({}, CHOICES).kind).toBe("unknown");
    expect(renderVoteChoice("{}", CHOICES).kind).toBe("unknown");
    expect(renderVoteChoice('{"1": 0.5}', CHOICES).kind).toBe("unknown");
    expect(renderVoteChoice("not json", CHOICES).kind).toBe("unknown");
    expect(renderVoteChoice([1.5], CHOICES).kind).toBe("unknown");
    expect(renderVoteChoice([], CHOICES).kind).toBe("unknown");
    expect(renderVoteChoice(null, CHOICES).kind).toBe("unknown");
  });
});
