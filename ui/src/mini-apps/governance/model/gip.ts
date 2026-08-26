// GIP title identity — dbt macros/governance/parse_gip_number.sql is the
// DEFINITION OF RECORD: an ANCHORED leading identity (optional whitespace /
// zero-width chars / '#', [...]/(...)  prefixes, optional "re-do of:"), so a
// mid-title mention yields null. Dialect twin of GIP_PATTERN in
// governance_explorer.py; escape syntax differs per dialect (\x{200B} in RE2
// vs ​ here), so parity is pinned by the shared fixture table
// (tests/gip_fixtures.py, mirrored in __tests__/gip.test.ts) evaluated in
// each engine — never by string comparison. Note JS \s is Unicode-wide while
// RE2's is ASCII-only (accepted divergence; this regex is display-only —
// cross-source linking happens server-side). GIP-0 is a phantom: the > 0
// guard below mirrors the dbt consumers' gip_number > 0 filter.

const GIP_RE =
  /^[\s​﻿#]*(?:\[[^\]]*\]\s*)*(?:\([^)]*\)\s*)*(?:re-?do of:\s*)?GIP\s*-?\s*0*([0-9]+)/i;

export function extractGip(title: string): number | null {
  const match = GIP_RE.exec(title ?? "");
  if (!match) return null;
  const n = Number(match[1]);
  return Number.isFinite(n) && n > 0 ? n : null;
}
