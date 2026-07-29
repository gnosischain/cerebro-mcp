import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { WalletBoard } from "../components/WalletBoard";
import type { WalletHolding } from "../model/treasuryWallets";

function wallet(overrides: Partial<WalletHolding> = {}): WalletHolding {
  return {
    chainId: 1,
    wallet: "0xaaaa000000000000000000000000000000000001",
    isLtd: false,
    tokensHeld: 5,
    unnamedPositions: 1,
    gnoUnits: 10,
    gnoUsd: 1000,
    ...overrides,
  };
}

describe("WalletBoard", () => {
  it("heads the USD column 'GNO value', never 'wallet value'", () => {
    // treasury_by_wallet carries no per-token composition, so a wallet's TOTAL
    // is not derivable here at any price coverage. Labelling a GNO-only figure
    // as the wallet's value would be a wrong number, not a partial one.
    const html = renderToStaticMarkup(<WalletBoard wallets={[wallet()]} />);
    expect(html).toContain("GNO value");
    expect(html.toLowerCase()).not.toContain("wallet value");
  });

  it("renders an unpriced wallet as 'unpriced', not $0", () => {
    const html = renderToStaticMarkup(<WalletBoard wallets={[wallet({ gnoUsd: null })]} />);
    expect(html).toContain("unpriced");
    expect(html).not.toContain("$0");
  });

  it("renders a wallet holding no GNO as 'no GNO', not $0.00", () => {
    // "$0.00" in a column headed GNO VALUE, on a row showing 98 tokens held,
    // reads as "this wallet is worth nothing". It holds no GNO; what the rest
    // is worth is not measured on this dataset at all.
    const html = renderToStaticMarkup(
      <WalletBoard wallets={[wallet({ gnoUnits: 0, gnoUsd: 0 })]} />,
    );
    expect(html).toContain("no GNO");
    expect(html).not.toContain("$0.00");
  });

  it("renders a dust balance at real precision — never rounded to 0", () => {
    // Printing 1e-9 as "0" makes the stronger claim that the treasury exited
    // the position.
    const html = renderToStaticMarkup(
      <WalletBoard wallets={[wallet({ gnoUnits: 0.000000001, gnoUsd: null })]} />,
    );
    expect(html).toMatch(/1(\.0+)?e-9|0\.000000001/);
  });

  it("shows a null count as a dash — fmtNum(null) would print 0", () => {
    const html = renderToStaticMarkup(
      <WalletBoard wallets={[wallet({ tokensHeld: null, unnamedPositions: null })]} />,
    );
    expect(html).toContain("—");
  });

  it("renders no token symbols at all — only the address and the Ltd. badge", () => {
    const html = renderToStaticMarkup(<WalletBoard wallets={[wallet({ isLtd: true })]} />);
    expect(html).toContain("Ltd.");
    expect(html).toContain("0xaaaa");
  });

  it("discloses the Ltd. exclusion, or an empty Ltd column reads as 'none exists'", () => {
    const shown = renderToStaticMarkup(<WalletBoard wallets={[wallet()]} ltdExcluded />);
    expect(shown).toContain("excluded by the toolbar filter");
    const hidden = renderToStaticMarkup(<WalletBoard wallets={[wallet()]} />);
    expect(hidden).not.toContain("excluded by the toolbar filter");
  });

  it("says how many wallets are not shown rather than truncating silently", () => {
    const wallets = Array.from({ length: 30 }, (_, index) =>
      wallet({ wallet: `0x${String(index).padStart(40, "0")}`, gnoUsd: 30 - index }));
    const html = renderToStaticMarkup(<WalletBoard wallets={wallets} maxRows={25} />);
    expect(html).toContain("Showing 25 of 30 wallets");
    expect(html).toContain("5 not shown");
  });
});
