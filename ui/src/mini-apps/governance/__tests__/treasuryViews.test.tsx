// @vitest-environment jsdom

// Render tests for the treasury section tabs, against the dev fixture (which
// mirrors the SQL contract). The regressions pinned here are the ones the
// user reported: "All" showed only Ethereum wallets, and the history captions
// talked about today's spot price and listed spoofed tokens by name.

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import contract from "../model/treasuryColumns.json";
import { T, W_GNOSIS_ONLY, W_LTD } from "../devFixtureTreasury";
import { shortAddr } from "../../../utils/format";
import { SCOPE_NOTE } from "../model/treasuryCopy";
import { TreasurySection } from "../sections/TreasurySection";
import type { TreasuryTabId } from "../model/treasuryTabs";
import { decode, failedDescriptor, treasuryCtx } from "./treasuryCtx";
import type { TreasuryViewState } from "../state/treasuryView";

const COLUMNS = contract.datasets as Record<string, string[]>;

function render(view: Partial<TreasuryViewState>, opts: Parameters<typeof treasuryCtx>[0] = {}): string {
  return decode(renderToStaticMarkup(<TreasurySection ctx={treasuryCtx({ ...opts, view })} />));
}

const TABS: TreasuryTabId[] = ["overview", "assets", "wallets", "history"];

describe("Wallets tab", () => {
  it("REGRESSION: chain 'All' lists the Gnosis-only wallet (it used to scope to Ethereum)", () => {
    const html = render({ tab: "wallets", chain: 0 });
    expect(html).toContain(shortAddr(W_GNOSIS_ONLY));
    expect(html).toContain("24 addresses · 47 wallet-chain pairs");
  });

  it("chain Ethereum hides the Gnosis-only wallet — and says so", () => {
    const html = render({ tab: "wallets", chain: 1 });
    expect(html).not.toContain(shortAddr(W_GNOSIS_ONLY));
    expect(html).toContain("tracked only on the other chain");
  });

  it("lists labelled wallets with nothing on a chain (zeros) — last, never dropped", () => {
    const html = render({ tab: "wallets", chain: 1 });
    expect(html).toContain("23 addresses · 23 wallet-chain pairs");
    expect(html).toContain("No positions.");
  });

  it("excluding Gnosis Ltd. hides the Ltd. wallet on both chains and counts it", () => {
    const shown = render({ tab: "wallets" });
    expect(shown).toContain(W_LTD.slice(0, 6));
    const html = render({ tab: "wallets", exLtd: true });
    expect(html).not.toContain("0x604e…350c");
    expect(html).toContain("Gnosis Ltd. excluded: 1 address (2 wallet-chain rows) hidden.");
  });

  it("shows every label with its address", () => {
    const html = render({ tab: "wallets" });
    expect(html).toContain("DAO Main Safe");
    expect(html).toContain("0x458c…5e6f");
    expect(html).toContain(W_GNOSIS_ONLY.slice(0, 6));
  });
});

describe("Overview tab", () => {
  const html = render({ tab: "overview" });

  it("splits the total into hub-priced and spot, and carries the ERC-20 scope note", () => {
    expect(html).toMatch(/hub \$[\d.]+M · spot \$[\d.]+K/);
    expect(html).toContain(SCOPE_NOTE);
    expect(html).toContain("Ethereum");
    expect(html).toContain("Gnosis Chain");
  });

  it("says 'spot pending' until the overlay lands, never a spot $0", () => {
    const pending = render({ tab: "overview" }, { overlay: false });
    expect(pending).toContain("spot pending");
    expect(pending).not.toContain("spot $0.00");
  });

  it("discloses gaps and hidden tokens as counts in the data notes", () => {
    expect(html).toContain("Blank months — Ethereum 1 (2023-02), Gnosis Chain 1 (2025-02)");
    expect(html).toContain("Partial months — Ethereum 1 (2026-07)");
    expect(html).toContain("Hidden and never valued:");
    expect(html).toContain("1 impersonation");
    expect(html).toContain("retired mirror");
    expect(html).toContain("spot quote was refused");
  });

  it("lists the top assets and wallets with a way to see them all", () => {
    expect(html).toContain("Top assets");
    expect(html).toContain("Top wallets");
    expect(html).toMatch(/View all \d+ →/);
  });

  it("reflects the Gnosis Ltd. exclusion in the headline", () => {
    const ex = render({ tab: "overview", exLtd: true });
    expect(ex).toContain("Token holdings ex-Ltd.");
    expect(ex).toContain("held only by Gnosis Ltd.");
  });
});

describe("Assets and History tabs", () => {
  it("the asset tab counts classes and offers the CSV export", () => {
    const html = render({ tab: "assets" });
    expect(html).toContain("How tokens are classified");
    expect(html).toContain("Export CSV (all classes)");
    expect(html).toContain("Hub-priced (");
  });

  it("the history tab names blank and partial months and states the valuation method", () => {
    const html = render({ tab: "history" });
    expect(html).toContain("Ethereum 2023-02: no census published — left blank, not a dip.");
    expect(html).toContain("Gnosis Chain 2025-02: nothing served upstream — left blank, not a dip.");
    expect(html).toContain("Ethereum 2026-07: partial upstream: 1 registry token not served (SAFE) — drawn from what was served.");
    expect(html).toContain("dbt price hub's daily USD price on each month-end");
    // The 1Y window only discloses the months it shows.
    const year = render({ tab: "history", range: "1y" });
    expect(year).not.toContain("2023-02");
    expect(year).toContain("2026-07");
  });
});

describe("failed datasets stay visible", () => {
  it("a query_failed holdings dataset renders the failed stub, not an empty table", () => {
    const html = render({ tab: "assets" }, {
      datasets: { treasury_holdings: failedDescriptor("treasury_holdings", COLUMNS.treasury_holdings) },
    });
    expect(html).toContain("This dataset failed to load.");
    expect(html).toContain("Memory limit exceeded");
  });

  it("a failed history dataset renders the stub on the history tab", () => {
    const html = render({ tab: "history" }, {
      datasets: { treasury_history: failedDescriptor("treasury_history", COLUMNS.treasury_history) },
    });
    expect(html).toContain("This dataset failed to load.");
  });

  it("a failed coverage dataset says month completeness is unknown", () => {
    const html = render({ tab: "history" }, {
      datasets: { treasury_history_coverage: failedDescriptor("treasury_history_coverage", COLUMNS.treasury_history_coverage) },
    });
    expect(html).toContain("Month completeness is unknown");
  });
});

describe("what must never appear while hidden tokens are off", () => {
  const FORBIDDEN_PHRASES = [
    "today's spot price",
    "no historical price feed",
    "Beyond the top series",
    "Excluded for want of a price",
    "constant-price",
  ];
  const SPAM = ["aave-sr.xyz", "ZKDROP", "Airdrop", T.FAKE_USDC_1.slice(0, 8), T.OBF_1.slice(0, 8), T.LURE_1.slice(0, 8), T.DROP_1.slice(0, 8), "\u{034F}"];

  for (const tab of TABS) {
    it(`${tab}: no revaluation captions and no spam names or addresses`, () => {
      const html = render({ tab, showHidden: false });
      for (const phrase of FORBIDDEN_PHRASES) expect(html, phrase).not.toContain(phrase);
      for (const spam of SPAM) expect(html, spam).not.toContain(spam);
    });
  }

  it("the spam does appear, with reasons, once hidden tokens are shown", () => {
    const html = render({ tab: "assets", showHidden: true, assetFilter: "hidden" });
    expect(html).toContain("aave-sr.xyz");
    expect(html).toContain("impersonation");
  });
});
