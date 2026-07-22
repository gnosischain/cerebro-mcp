import { describe, expect, it } from "vitest";

import { extractGip } from "../model/gip";

// Shared fixture list — asserted identically against the backend SQL pattern
// (?i)\bGIP[\s-]?0*([0-9]+) in tests/test_governance_explorer.py.
const FIXTURES: Array<[string, number | null]> = [
  ["GIP-151: Should GnosisDAO do the thing?", 151],
  ["GIP 152 - Treasury diversification", 152],
  ["gip-128", 128],
  ["GIP-0042", 42],
  ["AGIP-5", null], // \b rejects the AGIP prefix
  ["Community call schedule", null],
];

describe("extractGip — frozen shared pattern /\\bGIP[\\s-]?0*([0-9]+)/i", () => {
  it("matches the shared fixture list", () => {
    expect(extractGip("GIP-151: Should GnosisDAO do the thing?")).toBe(151);
    expect(extractGip("GIP 152 - Treasury diversification")).toBe(152);
    expect(extractGip("gip-128")).toBe(128);
    expect(extractGip("GIP-0042")).toBe(42);
    expect(extractGip("AGIP-5")).toBeNull();
    expect(extractGip("Community call schedule")).toBeNull();
  });

  it("has no digit cap and no trailing boundary", () => {
    expect(extractGip("GIP-1234567")).toBe(1234567);
    expect(extractGip("GIP-151abc")).toBe(151); // no trailing boundary by design
  });

  it("word boundary rejects prefixed tokens but not separated ones", () => {
    expect(extractGip("AGIP-5")).toBeNull();
    expect(extractGip("preGIP-9")).toBeNull();
    expect(extractGip("Re: GIP-33 follow-up")).toBe(33);
    expect(extractGip("(GIP-7)")).toBe(7);
  });

  it("handles empty and null-ish input", () => {
    expect(extractGip("")).toBeNull();
    expect(extractGip(undefined as unknown as string)).toBeNull();
  });
});

// Guard against fixture drift: the table above documents intent for the
// cross-stack fixture file; keep it consistent with the direct assertions.
describe("fixture list consistency", () => {
  it("every fixture extracts its expected value", () => {
    for (const [title, expected] of FIXTURES) {
      expect(extractGip(title), title).toBe(expected);
    }
  });
});
