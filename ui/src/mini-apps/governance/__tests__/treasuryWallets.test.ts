import { describe, expect, it } from "vitest";

import {
  GNO_TOKENS,
  sortWallets,
  walletHoldings,
  walletSortValue,
  type WalletHolding,
} from "../model/treasuryWallets";
import type { PriceSource } from "../model/treasuryPricing";
import {
  DEFAULT_TREASURY_TAB,
  TREASURY_TABS,
  groupsForTab,
  isTreasuryTab,
  toTreasuryTab,
} from "../model/treasuryTabs";
import { SECTION_GROUPS } from "../model/datasetGroups";

const SPOT: PriceSource = {
  kind: "spot",
  at: "2026-07-29T00:00:00Z",
  byChain: { "1": { [GNO_TOKENS[1]]: 100 } },
};

function row(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    chain_id: 1,
    wallet_address: "0xAAAA000000000000000000000000000000000001",
    is_ltd: 0,
    tokens_held: 5,
    unnamed_positions: 1,
    gno_units: 10,
    ...overrides,
  };
}

describe("walletHoldings", () => {
  it("prices GNO only, from the address, never from a symbol", () => {
    const [wallet] = walletHoldings([row()], SPOT);
    expect(wallet.gnoUnits).toBe(10);
    expect(wallet.gnoUsd).toBe(1000);
  });

  it("lowercases the wallet so a checksummed and a lowercase row are one wallet", () => {
    const [wallet] = walletHoldings([row()], null);
    expect(wallet.wallet).toBe("0xaaaa000000000000000000000000000000000001");
  });

  it("reads is_ltd as a flag whether it arrives as 0/1, a bool or a string", () => {
    // finite() returns null for booleans, so the obvious finite(row.is_ltd)
    // would read EVERY Ltd wallet as not-Ltd.
    for (const value of [1, true, "true", "1"]) {
      expect(walletHoldings([row({ is_ltd: value })], null)[0].isLtd).toBe(true);
    }
    for (const value of [0, false, "false", "", null]) {
      expect(walletHoldings([row({ is_ltd: value })], null)[0].isLtd).toBe(false);
    }
  });

  it("drops rows without a chain or a wallet rather than showing an unattributable balance", () => {
    expect(walletHoldings([row({ wallet_address: "" }), row({ chain_id: null })], null))
      .toHaveLength(0);
  });

  it("leaves gnoUsd null when no quote exists — unpriced is not zero", () => {
    const [wallet] = walletHoldings([row({ chain_id: 100 })], SPOT);
    expect(wallet.gnoUnits).toBe(10);
    expect(wallet.gnoUsd).toBeNull();
  });

  it("sorts unpriced and unknown LAST on every key, never as smallest", () => {
    const wallets = walletHoldings(
      [
        row({ wallet_address: "0xbb", gno_units: null }),
        row({ wallet_address: "0xcc", gno_units: 50 }),
        row({ wallet_address: "0xdd", gno_units: 1 }),
      ],
      SPOT,
    );
    expect(wallets.map((wallet) => wallet.gnoUnits)).toEqual([50, 1, null]);
    for (const key of ["gnoUsd", "gnoUnits", "tokensHeld", "unnamedPositions"] as const) {
      const ordered = sortWallets(wallets, key);
      const values = ordered.map((wallet) => walletSortValue(wallet, key));
      const firstNull = values.indexOf(null);
      if (firstNull >= 0) {
        expect(values.slice(firstNull).every((value) => value === null)).toBe(true);
      }
    }
  });

  it("orders totally and stably: equal measures fall back to the address", () => {
    const wallets: WalletHolding[] = walletHoldings(
      [row({ wallet_address: "0xbb" }), row({ wallet_address: "0xaa" })],
      null,
    );
    expect(sortWallets(wallets, "tokensHeld").map((wallet) => wallet.wallet))
      .toEqual(["0xaa", "0xbb"]);
  });
});

describe("treasury tabs", () => {
  it("names only groups that exist in SECTION_GROUPS.treasury", () => {
    const known = Object.keys(SECTION_GROUPS.treasury);
    for (const tab of TREASURY_TABS) {
      for (const group of tab.groups) expect(known).toContain(group);
    }
  });

  it("covers every treasury group across the tab set — nothing is unreachable", () => {
    const reached = new Set(TREASURY_TABS.flatMap((tab) => [...tab.groups]));
    expect([...reached].sort()).toEqual(Object.keys(SECTION_GROUPS.treasury).sort());
  });

  it("coerces an unknown or absent tab to the default rather than rendering nothing", () => {
    for (const value of ["", "nope", null, undefined, 3]) {
      expect(isTreasuryTab(value)).toBe(false);
      expect(toTreasuryTab(value)).toBe(DEFAULT_TREASURY_TAB);
    }
    expect(toTreasuryTab("wallets")).toBe("wallets");
    expect(groupsForTab("wallets")).toContain("core");
    expect(groupsForTab("nope" as never)).toEqual(["core"]);
  });
});
