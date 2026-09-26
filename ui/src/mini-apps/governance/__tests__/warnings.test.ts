import { describe, expect, it } from "vitest";

import { QUIET_WARNINGS, WARNING_COPY, resolveWarnings } from "../state/warnings";

const TREASURY_CODES = [
  "treasury_asof_partial", "treasury_chain_unserved", "treasury_tokens_carried",
  "treasury_price_hub_stale", "treasury_history_partial", "treasury_history_gap",
  "treasury_history_unpublished",
];

describe("warning strip", () => {
  it("never renders a treasury code raw", () => {
    // The regression: "treasury_history_partial" sat in a yellow chip above the
    // treasury because the strip passes unknown strings through unchanged.
    const shown = resolveWarnings({ coverage_warnings: TREASURY_CODES, warnings: [] });
    for (const line of shown) expect(line).not.toMatch(/^[a-z_]+$/);
    for (const code of TREASURY_CODES) expect(WARNING_COPY[code]).toMatch(/ /);
  });

  it("keeps what a panel already discloses out of the banner", () => {
    // Toolbar as-of chips and the history GapNote / Data notes say these where
    // the number is; a banner would repeat them on every treasury view.
    const shown = resolveWarnings({
      coverage_warnings: ["treasury_history_partial", "treasury_history_gap", "treasury_asof_partial"],
      warnings: ["treasury_chain_unserved", "treasury_history_unpublished"],
    });
    expect(shown).toEqual([]);
  });

  it("banners what no panel shows: carried tokens and a lagging price hub", () => {
    const shown = resolveWarnings({
      coverage_warnings: ["treasury_tokens_carried"], warnings: ["treasury_price_hub_stale"],
    });
    expect(shown).toEqual([WARNING_COPY.treasury_tokens_carried, WARNING_COPY.treasury_price_hub_stale]);
    expect(QUIET_WARNINGS.has("treasury_tokens_carried")).toBe(false);
  });

  it("passes free text through and de-duplicates", () => {
    const shown = resolveWarnings({
      coverage_warnings: ["query_failed", "Upstream said no."], warnings: ["query_failed"],
    });
    expect(shown).toEqual([WARNING_COPY.query_failed, "Upstream said no."]);
  });
});
