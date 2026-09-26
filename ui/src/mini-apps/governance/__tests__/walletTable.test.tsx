// The wallet table: every address, no cap, labels WITH addresses, an
// unpriced value as a dash (never $0), and a footer that says what the
// filters hide. Rendered with renderToStaticMarkup (no testing-library).

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { WalletTable, walletValueText } from "../components/treasury/WalletTable";
import type { WalletRow } from "../model/treasuryRows";
import { walletGroups } from "../model/treasuryWallets";

function row(index: number, overrides: Partial<WalletRow> = {}): WalletRow {
  return {
    chainId: 1,
    wallet: `0x${String(index + 1).padStart(40, "0")}`,
    label: index % 7 === 0 ? "" : `Safe ${index}`,
    labelSource: "koeppelmann/GnosisDAO_treasury README (community list)",
    isLtd: false,
    tokensHeld: 3,
    pricedPositions: 2,
    unpricedPositions: 1,
    hiddenPositions: 0,
    gnoUnits: 100 + index,
    navUsd: 1000 * (index + 1),
    asOf: "2026-09-24",
    asOfStatus: "",
    anchorBlock: null,
    ...overrides,
  };
}

const html = (rows: WalletRow[], opts: { chain?: 0 | 1 | 100; exLtd?: boolean } = {}) => renderToStaticMarkup(
  <WalletTable
    grouping={walletGroups(rows, { chain: opts.chain ?? 0, exLtd: opts.exLtd ?? false })}
    chainFilter={opts.chain ?? 0}
    exLtd={opts.exLtd ?? false}
    onOpen={() => {}}
  />,
);

describe("WalletTable", () => {
  it("renders all 47 wallets — there is no cap", () => {
    const rows = Array.from({ length: 47 }, (_, index) => row(index));
    const out = html(rows);
    expect((out.match(/class="gov-trs-row"/g) ?? []).length).toBe(47);
    expect(out).toContain("47 addresses");
    expect(out).not.toContain("not shown");
  });

  it("shows the label AND the address, with the attribution in the tooltip", () => {
    const out = html([row(1)]);
    expect(out).toContain("Safe 1");
    expect(out).toContain("0x0000…0002");
    expect(out).toContain("Label from koeppelmann/GnosisDAO_treasury README");
  });

  it("an unlabelled wallet shows its address alone, never a placeholder name", () => {
    const out = html([row(0)]);
    expect(out).toContain("gov-trs-wallet__addr--solo");
    expect(out).toContain("No community label for this address.");
  });

  it("an unpriced value is a dash — never $0", () => {
    const unpriced = row(3, { navUsd: 0, pricedPositions: 0, unpricedPositions: 4 });
    expect(walletValueText(unpriced)).toBe("—");
    expect(walletValueText(row(3, { navUsd: null }))).toBe("—");
    expect(walletValueText(row(3, { navUsd: 1234 }))).toBe("$1.2K");
    const out = html([unpriced]);
    expect(out).not.toContain("$0");
    expect(out).toContain("unknown, not zero");
  });

  it("one row per address with a chip per chain", () => {
    const out = html([row(1), row(1, { chainId: 100, navUsd: 5 })]);
    expect((out.match(/class="gov-trs-row"/g) ?? []).length).toBe(1);
    expect(out).toContain("Ethereum");
    expect(out).toContain("Gnosis Chain");
    expect(out).toContain("2 wallet-chain pairs");
  });

  it("the footer says what the chain filter and the Ltd. exclusion hide", () => {
    const rows = [row(1), row(1, { chainId: 100 }), row(2, { chainId: 100 }), row(3, { isLtd: true }), row(3, { chainId: 100, isLtd: true })];
    const eth = html(rows, { chain: 1 });
    // Hidden: row 1's and row 3's Gnosis Chain rows, and row 2 entirely.
    expect(eth).toContain("The Ethereum filter hides 3 wallet-chain rows (1 address tracked only on the other chain)");
    const ex = html(rows, { exLtd: true });
    expect(ex).toContain("Gnosis Ltd. excluded: 1 address (2 wallet-chain rows) hidden.");
    expect(ex).not.toContain("Safe 3");
  });

  it("counts hidden positions without listing them", () => {
    const out = html([row(1, { hiddenPositions: 4 })]);
    expect(out).toContain("+4 hidden");
  });
});
