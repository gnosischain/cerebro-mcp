// @vitest-environment jsdom

// Render tests for the treasury entity pages (wallet, asset), against the
// fixture bundles for BOTH chains.

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TreasuryTokenDetail } from "../detail/TreasuryTokenDetail";
import { TreasuryWalletDetail } from "../detail/TreasuryWalletDetail";
import { T, W_EMPTY_ETH, W_GNOSIS_ONLY, W_MAIN } from "../devFixtureTreasury";
import { SCOPE_NOTE } from "../model/treasuryCopy";
import { decode, treasuryCtx } from "./treasuryCtx";

function wallet(identifier: string, view = {}): string {
  return decode(renderToStaticMarkup(
    <TreasuryWalletDetail ctx={treasuryCtx({ entity: { type: "treasury_wallet", identifier }, view })} />,
  ));
}

function token(identifier: string, view = {}): string {
  return decode(renderToStaticMarkup(
    <TreasuryTokenDetail ctx={treasuryCtx({ entity: { type: "treasury_token", identifier }, view })} />,
  ));
}

describe("wallet page", () => {
  const eth = wallet(`1:${W_MAIN}`);

  it("shows the label, the FULL address and the label's attribution", () => {
    expect(eth).toContain("DAO Main Safe");
    expect(eth).toContain(W_MAIN);
    expect(eth).toContain("Label from koeppelmann/GnosisDAO_treasury README (community list)");
    expect(eth).toContain("Treasury wallet · Ethereum");
  });

  it("has a chain switcher with both chains, and points at the other chain's holdings", () => {
    expect(eth).toContain('aria-label="Wallet chain"');
    expect(eth).toMatch(/Ethereum<\/span><span class="gov-trs-switcher__note">\$/);
    expect(eth).toMatch(/Gnosis Chain<\/span><span class="gov-trs-switcher__note">\$/);
    expect(eth).toMatch(/Also holds \$[\d.]+M on Gnosis Chain →/);
  });

  it("REGRESSION: a short Ethereum history points at the long Gnosis Chain one", () => {
    expect(eth).toContain("Holdings on Ethereum start 2026-07");
    expect(eth).toContain("Gnosis Chain history since 2020-07 →");
    expect(eth).toContain("Tracked since");
    // 2026-07 was only partly served upstream: drawn, and named.
    expect(eth).toContain("Ethereum 2026-07: partial upstream: 1 registry token not served (SAFE) — drawn from what was served.");
  });

  it("the same wallet on Gnosis Chain has its own page, without the short-history note", () => {
    const gnosis = wallet(`100:${W_MAIN}`);
    expect(gnosis).toContain("Treasury wallet · Gnosis Chain");
    expect(gnosis).toContain("2020-07");
    expect(gnosis).not.toContain("Holdings on Gnosis Chain start");
    expect(gnosis).toMatch(/Also holds \$[\d.]+M on Ethereum →/);
  });

  it("a labelled wallet with nothing valued on a chain shows a dash, never $0", () => {
    const empty = wallet(`1:${W_EMPTY_ETH}`);
    expect(empty).toContain("no valued positions on this chain");
    expect(empty).not.toContain(">$0.00<");
    expect(empty).toContain("Show hidden tokens (1)");
  });

  it("greys out a chain the address is not tracked on", () => {
    const only = wallet(`100:${W_GNOSIS_ONLY}`);
    expect(only).toContain("not tracked");
    expect(only).toMatch(/disabled="" title="This address is not in the Ethereum treasury census"/);
  });

  it("an unlabelled address never claims a label source (the SQL sends one on every row)", () => {
    const only = wallet(`100:${W_GNOSIS_ONLY}`);
    expect(only).toContain("Unlabelled wallet");
    expect(only).toContain("No community label for this address.");
    expect(only).not.toContain("Label from");
  });

  it("an address outside a chain's census gets the SQL's zero row: a dash, never $0", () => {
    const off = wallet(`1:${W_GNOSIS_ONLY}`);
    expect(off).toContain("Treasury wallet · Ethereum");
    expect(off).toContain("no valued positions on this chain");
    expect(off).not.toContain(">$0.00<");
    expect(off).toContain("not tracked");
  });

  it("carries the scope note and the hub/spot split (the spot part only when something is spot-valued)", () => {
    expect(eth).toContain(SCOPE_NOTE);
    expect(eth).toMatch(/hub \$[\d.]+M</);
    expect(eth).not.toContain("spot $0");
    // The Investments Safe holds GRT, a listed token valued at CoinGecko spot.
    const grt = wallet(`1:0x${"d006".repeat(10)}`);
    expect(grt).toMatch(/hub \$[\d.]+[KM] · spot \$[\d.]+[KM]/);
  });

  it("hides spam by default but counts it", () => {
    expect(eth).toContain("Show hidden tokens (1)");
    expect(eth).not.toContain("ZKDROP");
    const shown = wallet(`1:${W_MAIN}`, { showHidden: true });
    expect(shown).toContain("ZKDROP");
    expect(shown).toContain("mass airdrop");
  });

  it("never mentions today's spot price in a history caption", () => {
    expect(eth).not.toContain("today's spot price");
    expect(eth).not.toContain("constant-price");
    expect(eth).toContain("dbt price hub's daily USD price on each month-end");
  });
});

describe("asset page", () => {
  it("a registry asset: trusted symbol, class badge, hub price and date, labelled holders", () => {
    const html = token(`1:${T.GNO_1}`);
    expect(html).toContain("Treasury asset · Ethereum");
    expect(html).toContain(">GNO<");
    expect(html).toContain("Hub-priced");
    expect(html).toContain("dbt price hub · 2026-09-24");
    expect(html).toContain("DAO Main Safe");
    expect(html).toContain("Gnosis Ltd.");
    expect(html).toContain(T.GNO_1);
    // The same asset on Gnosis Chain is one click away.
    expect(html).toMatch(/The same registry asset on Gnosis Chain/);
  });

  it("excluding Gnosis Ltd. hides its holder row and counts it", () => {
    const html = token(`1:${T.GNO_1}`, { exLtd: true });
    expect(html).toContain("Value ex-Ltd.");
    expect(html).toContain("1 Gnosis Ltd. wallet is hidden by the exclusion.");
    expect(html).not.toContain("0x604e…350c");
  });

  it("a spam token: banner with the reason in words, never valued, address shown", () => {
    const html = token(`1:${T.FAKE_USDC_1}`);
    expect(html).toContain("Hidden by default, never valued.");
    expect(html).toContain("Flagged as impersonation");
    expect(html).toContain("Spam · Impersonation");
    expect(html).toContain("not valued");
    expect(html).toContain(T.FAKE_USDC_1);
    // The balance exceeds the fake token's own supply: no fake percentage.
    expect(html).toContain("> supply");
    expect(html).toContain("No price history: this token is not in the reviewed price registry.");
  });

  it("a listed token valued at spot says so, with the capture time", () => {
    const html = token(`1:${T.GRT_1}`);
    expect(html).toContain("CoinGecko spot · 2026-09-25 08:00 UTC");
    expect(html).toContain("Listed");
    expect(html).toContain("No dbt price hub series for this token");
  });

  it("a peg-proxy token is hub-priced and says through which series", () => {
    const html = token(`1:${T.STETH_1}`);
    expect(html).toContain("Hub-priced");
    expect(html).toContain("hub peg proxy · 2026-09-24");
    expect(html).toContain("peg proxy");
  });

  it("a token no longer held: 'not held on <as-of>', while its history stays visible", () => {
    const html = token(`1:${T.BAL_1}`);
    expect(html).toContain("not held on 2026-09-24");
    expect(html).toContain("Not held on 2026-09-24: the treasury holds none of this token today.");
    expect(html).toContain("Hub price series: BAL");
    // Not a failed load: no error card anywhere.
    expect(html).not.toContain("This dataset failed to load.");
    expect(html).toContain("Holdings over time");
    expect(html).toContain("Price history");
  });

  it("an unverified token shows its sanitized symbol with the address and the collision count", () => {
    const html = token(`1:${T.RAID_1}`);
    expect(html).toContain("RAID \u{2694}");
    expect(html).not.toContain("\u{FE0F}");
    expect(html).toContain("Unverified");
    expect(html).toContain("Others claiming this symbol");
  });
});
