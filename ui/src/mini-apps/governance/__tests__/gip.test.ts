import { describe, expect, it } from "vitest";

import { extractGip } from "../model/gip";

// Shared fixture table — content-identical to tests/gip_fixtures.py, which
// the Python and live-ClickHouse suites evaluate against their own dialect
// renderings of dbt's canonical parse_gip_number pattern. The three dialects
// cannot share one regex string (escape syntax differs), so THIS table is
// what pins them together. Edit both files or neither.
const FIXTURES: Array<[string, number | null]> = [
  // Plain identities.
  ["GIP-151: Should GnosisDAO fund X", 151],
  ["GIP 152 - Treasury topup", 152],
  ["gip-128", 128],
  ["GIP-0042 legacy numbering", 42],
  ["GIP - 77", 77],
  // No digit cap, no trailing boundary.
  ["GIP-1234567", 1234567],
  ["GIP-151abc", 151],
  // Canonical prefixes before the identity.
  ["[Draft] GIP-90: x", 90],
  ["(Signaling) GIP-64 vote", 64],
  ["# GIP-12", 12],
  ["​GIP-45", 45],
  ["Redo of: GIP-33", 33],
  ["Re-do of: GIP-33", 33],
  ["[RE-RUN] (redo) GIP-8", 8],
  // Mid-title mentions are NOT identities (anchored pattern).
  ["discussing gip-128 here", null],
  ["Re: GIP-33 follow-up", null],
  ["(GIP-7)", null],
  // Other DAOs' numbering and non-matches.
  ["AGIP-5 is another DAO's numbering", null],
  ["preGIP-9", null],
  ["GIP:151 colon is not a separator", null],
  ["no token here", null],
  ["", null],
  // Phantom guard: GIP-0 is never an identity.
  ["GIP-0", null],
];

describe("extractGip — canonical anchored title identity (dbt parse_gip_number)", () => {
  it("matches the shared fixture table", () => {
    for (const [title, expected] of FIXTURES) {
      expect(extractGip(title), JSON.stringify(title)).toBe(expected);
    }
  });

  it("is an identity, not a mention scan", () => {
    // The load-bearing change of WL-039: a title that merely MENTIONS a GIP
    // is not that GIP. Body-mention extraction lives server-side only, as
    // GIP_MENTION_PATTERN_SQL in graph_edges.sql.
    expect(extractGip("report on GIP-18")).toBeNull();
    expect(extractGip("GIP-18 report")).toBe(18);
  });

  it("handles empty and null-ish input", () => {
    expect(extractGip("")).toBeNull();
    expect(extractGip(undefined as unknown as string)).toBeNull();
  });
});
