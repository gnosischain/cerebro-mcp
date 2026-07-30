// @vitest-environment jsdom

// The "priced only" toggle, and the two ways it can mislead.
//
// (1) It must DEFAULT OFF. `usd` is null on every holding until the async price
//     overlay lands, so a default-on filter renders an empty board on first
//     paint — indistinguishable from a load failure.
// (2) It must SAY what else it hides. Priced/unpriced is the de-facto spoof
//     signal here: the look-alike tokens are almost all unpriced, so hiding
//     unpriced rows also hides the spoofs. A user who does not know that will
//     conclude the treasury holds no fakes.
//
// Rendered with `renderToStaticMarkup`, matching the other .tsx tests here —
// @testing-library is not a dependency, so a CLICK cannot be simulated. The
// filtered state is therefore covered by asserting the predicate agrees with the
// counts the label promises, rather than by driving the button.

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TokenBoard } from "../components/TokenBoard";
import { priceCoverage } from "../model/treasuryPricing";

function holding(token: string, symbol: string, usd: number | null) {
  return {
    chainId: 1, token, symbol, units: 100, raw: "100", decimals: 18,
    metadataStatus: "ok", wallets: 1, supplyShare: null, usd,
    ambiguous: false, price: usd === null ? null : 1,
  } as never;
}

const HOLDINGS = [
  holding(`0x${"11".repeat(20)}`, "GNO", 5000),
  holding(`0x${"22".repeat(20)}`, "COW", 2500),
  // Two unpriced look-alikes, which is the real shape: the spoofs are unpriced.
  holding(`0x${"33".repeat(20)}`, "USDC", null),
  holding(`0x${"44".repeat(20)}`, "USDC", null),
];

const html = (holdings: unknown[]) =>
  renderToStaticMarkup(<TokenBoard holdings={holdings as never} />);

describe("TokenBoard priced-only toggle", () => {
  it("defaults OFF, so the board is populated before prices arrive", () => {
    const out = html(HOLDINGS);
    expect(out).toContain('aria-pressed="false"');
    for (const symbol of ["GNO", "COW", "USDC"]) expect(out).toContain(symbol);
  });

  it("labels the toggle with the priced count, so it is not a blind switch", () => {
    expect(html(HOLDINGS)).toContain("Priced only (2/4)");
  });

  it("states the coverage and that unpriced is unmeasured, never $0", () => {
    const out = html(HOLDINGS);
    expect(out).toContain("2 of 4 held tokens priced");
    expect(out).toContain("unmeasured, not worthless");
  });

  it("disables itself when NO price has loaded, rather than emptying the board", () => {
    // Both the first-paint state AND a chain-100-scoped view, where nothing is
    // priced at all — the control must not offer to empty the board for a
    // reason unrelated to any individual token.
    const none = HOLDINGS.map((h) => ({ ...(h as object), usd: null }));
    const out = html(none);
    expect(out).toContain("disabled");
    expect(out).toContain("No USD prices loaded");
    expect(out).toContain("GNO");
  });

  it("filters on the same predicate its counts come from", () => {
    // The toggle hides `usd === null`; the label promises `priced/total`. If
    // those disagreed the count would advertise a different board than it shows.
    const { priced, total } = priceCoverage(HOLDINGS as never);
    const kept = (HOLDINGS as unknown as Array<{ usd: number | null }>)
      .filter((h) => h.usd !== null);
    expect(kept.length).toBe(priced);
    expect(total - priced).toBe(2);
  });
});
