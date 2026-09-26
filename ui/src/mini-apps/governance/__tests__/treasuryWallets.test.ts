// Wallets grouped by ADDRESS across chains, with the chain and Gnosis Ltd.
// filters applied client-side — and what they hide COUNTED.

import { describe, expect, it } from "vitest";

import { MOCK_PAYLOAD } from "../devFixture";
import { W_GNOSIS_ONLY, W_LTD, W_MAIN } from "../devFixtureTreasury";
import { parseWallets, type WalletRow } from "../model/treasuryRows";
import {
  matchesQuery,
  primaryChainOf,
  sortWalletGroups,
  walletGroups,
} from "../model/treasuryWallets";

const WALLETS = parseWallets({
  columns: MOCK_PAYLOAD.datasets!.treasury_by_wallet.columns.map((column) => column.name),
  rows: MOCK_PAYLOAD.datasets!.treasury_by_wallet.preview_rows,
});

function row(overrides: Partial<WalletRow>): WalletRow {
  return {
    chainId: 1, wallet: "0x0000000000000000000000000000000000000001", label: "", labelSource: "",
    isLtd: false, tokensHeld: 1, pricedPositions: 1, unpricedPositions: 0, hiddenPositions: 0,
    gnoUnits: 0, navUsd: 0, asOf: "2026-09-24", asOfStatus: "", anchorBlock: null,
    ...overrides,
  };
}

describe("walletGroups", () => {
  it("REGRESSION: 'All' shows BOTH chains' wallets — one row per address", () => {
    const grouping = walletGroups(WALLETS, { chain: 0, exLtd: false });
    expect(grouping.groups).toHaveLength(24);
    expect(grouping.pairs).toBe(47);
    expect(grouping.groups.some((group) => group.address === W_GNOSIS_ONLY)).toBe(true);
    const main = grouping.groups.find((group) => group.address === W_MAIN)!;
    expect(main.chains.map((entry) => entry.chainId)).toEqual([1, 100]);
    expect(main.label).toBe("DAO Main Safe");
    expect(grouping.hiddenByChain).toEqual({ pairs: 0, addresses: 0 });
  });

  it("a chain filter hides the other chain's rows and COUNTS what it hid", () => {
    const eth = walletGroups(WALLETS, { chain: 1, exLtd: false });
    expect(eth.groups).toHaveLength(23);
    expect(eth.groups.some((group) => group.address === W_GNOSIS_ONLY)).toBe(false);
    expect(eth.hiddenByChain).toEqual({ pairs: 24, addresses: 1 });
  });

  it("excluding Gnosis Ltd. drops its rows on every chain and counts them", () => {
    const ex = walletGroups(WALLETS, { chain: 0, exLtd: true });
    expect(ex.groups.some((group) => group.address === W_LTD)).toBe(false);
    expect(ex.hiddenByLtd).toEqual({ pairs: 2, addresses: 1 });
  });

  it("sums per-chain figures, leaving unknown as null rather than 0", () => {
    const grouping = walletGroups([
      row({ chainId: 1, navUsd: 10, gnoUnits: 1 }),
      row({ chainId: 100, navUsd: 5, gnoUnits: null }),
      row({ chainId: 1, wallet: "0x0000000000000000000000000000000000000002", navUsd: null, gnoUnits: null }),
    ], { chain: 0, exLtd: false });
    const first = grouping.groups.find((group) => group.address.endsWith("1"))!;
    expect(first.navUsd).toBe(15);
    expect(first.gnoUnits).toBe(1);
    const second = grouping.groups.find((group) => group.address.endsWith("2"))!;
    expect(second.navUsd).toBeNull();
  });
});

describe("sorting, search, primary chain", () => {
  const groups = walletGroups(WALLETS, { chain: 0, exLtd: false }).groups;

  it("sorts by value (nulls last), GNO, tokens or name, with a stable address tie-break", () => {
    const byValue = sortWalletGroups(groups, "value");
    for (let index = 1; index < byValue.length; index += 1) {
      expect((byValue[index - 1].navUsd ?? -1) >= (byValue[index].navUsd ?? -1)).toBe(true);
    }
    const byName = sortWalletGroups(groups, "name");
    // Labelled wallets alphabetically, the unlabelled one last.
    expect(byName[byName.length - 1].label).toBe("");
    expect(byName[0].label <= byName[1].label).toBe(true);
    expect(sortWalletGroups(groups, "gno")[0].gnoUnits).toBe(Math.max(...groups.map((group) => group.gnoUnits ?? 0)));
  });

  it("searches labels and any part of the address", () => {
    const main = groups.find((group) => group.address === W_MAIN)!;
    expect(matchesQuery(main, "main safe")).toBe(true);
    expect(matchesQuery(main, "5e6f")).toBe(true);
    expect(matchesQuery(main, "nope")).toBe(false);
    expect(matchesQuery(main, "  ")).toBe(true);
  });

  it("opens the chain holding the most value", () => {
    const main = groups.find((group) => group.address === W_MAIN)!;
    const eth = main.chains.find((entry) => entry.chainId === 1)!.navUsd ?? 0;
    const gnosis = main.chains.find((entry) => entry.chainId === 100)!.navUsd ?? 0;
    expect(primaryChainOf(main)).toBe(eth >= gnosis ? 1 : 100);
    expect(primaryChainOf({ chains: [row({ chainId: 100, navUsd: null }), row({ chainId: 1, navUsd: null })] })).toBe(1);
  });
});
