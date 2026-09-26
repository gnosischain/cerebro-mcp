// Treasury dataset parsers + the contract they read.
//
// treasuryColumns.json is THE contract shared with the backend SQL. The
// fixture must mirror it column-for-column, in order — a fixture that drifts
// makes a real server bug look like fixture noise (and vice versa).

import { describe, expect, it } from "vitest";

import contract from "../model/treasuryColumns.json";
import { MOCK_PAYLOAD } from "../devFixture";
import { T, treasuryEntityDatasets, W_GNOSIS_ONLY, W_MAIN } from "../devFixtureTreasury";
import { ENTITY_DATASETS, SECTION_GROUPS } from "../model/datasetGroups";
import {
  ASSET_CLASSES,
  AS_OF_STATUSES,
  COVERAGE_STATUSES,
  HISTORY_GRAINS,
  PRICE_SOURCES,
  SPAM_REASONS,
  TOKEN_CLASSES,
  bucket,
  civilFromDays,
  day,
  flag,
  parseCoverage,
  parseHistory,
  parseHoldings,
  parseHolders,
  parsePriceHistory,
  parseSummary,
  parseTokenDetail,
  parseWalletChains,
  parseWallets,
  tokenClassOf,
} from "../model/treasuryRows";
import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import type { RowDataset } from "../../shared/rowDataset";

const DATASETS = contract.datasets as Record<string, string[]>;

function asRows(descriptor: DatasetDescriptor | undefined): RowDataset | undefined {
  if (!descriptor) return undefined;
  return { columns: descriptor.columns.map((column) => column.name), rows: descriptor.preview_rows };
}

function columnsOf(descriptor: DatasetDescriptor): string[] {
  return descriptor.columns.map((column) => column.name);
}

describe("contract vocabularies", () => {
  it("mirror treasuryColumns.json exactly", () => {
    expect([...TOKEN_CLASSES]).toEqual(contract.vocab.token_class);
    expect([...SPAM_REASONS]).toEqual(contract.vocab.spam_reason);
    expect([...AS_OF_STATUSES]).toEqual(contract.vocab.as_of_status);
    expect([...COVERAGE_STATUSES]).toEqual(contract.vocab.coverage_status);
    expect([...HISTORY_GRAINS]).toEqual(contract.vocab.grain);
    expect([...PRICE_SOURCES]).toEqual(contract.vocab.price_source);
    expect([...ASSET_CLASSES]).toEqual(contract.vocab.asset_class);
  });

  it("the group and entity-bundle mirrors match the contract", () => {
    expect(SECTION_GROUPS.treasury).toEqual(contract.groups.treasury);
    expect(ENTITY_DATASETS.treasury_token).toEqual(contract.entity_bundles.treasury_token);
    expect(ENTITY_DATASETS.treasury_wallet).toEqual(contract.entity_bundles.treasury_wallet);
  });

  it("every grouped / bundled dataset has a column list in the contract", () => {
    const named = [
      ...Object.values(contract.groups.treasury).flat(),
      ...Object.values(contract.entity_bundles).flat(),
    ];
    for (const key of named) expect(DATASETS[key], key).toBeDefined();
  });
});

describe("fixture <-> contract parity", () => {
  const section = Object.values(contract.groups.treasury).flat();

  it("every section dataset in the fixture carries the contract's columns, in order", () => {
    for (const key of section) {
      const descriptor = MOCK_PAYLOAD.datasets![key];
      expect(descriptor, key).toBeDefined();
      expect(columnsOf(descriptor), key).toEqual(DATASETS[key]);
    }
  });

  it("every entity bundle, on BOTH chains, carries the contract's columns, in order", () => {
    const bundles: Array<["treasury_wallet" | "treasury_token", string]> = [
      ["treasury_wallet", `1:${W_MAIN}`],
      ["treasury_wallet", `100:${W_MAIN}`],
      ["treasury_wallet", `100:${W_GNOSIS_ONLY}`],
      ["treasury_token", `1:${T.GNO_1}`],
      ["treasury_token", `100:${T.GNO_100}`],
      ["treasury_token", `1:${T.FAKE_USDC_1}`],
    ];
    for (const [type, identifier] of bundles) {
      const datasets = treasuryEntityDatasets(type, identifier);
      expect(Object.keys(datasets).sort(), identifier).toEqual([...contract.entity_bundles[type]].sort());
      for (const [key, descriptor] of Object.entries(datasets)) {
        expect(columnsOf(descriptor), `${identifier} ${key}`).toEqual(DATASETS[key]);
      }
    }
  });

  it("every treasury descriptor in MOCK_PAYLOAD is a contract dataset", () => {
    for (const [key, descriptor] of Object.entries(MOCK_PAYLOAD.datasets!)) {
      if (!key.startsWith("treasury_")) continue;
      expect(DATASETS[key], key).toBeDefined();
      expect(columnsOf(descriptor), key).toEqual(DATASETS[key]);
    }
  });
});

describe("scalar readers", () => {
  it("day() accepts dates, datetimes and ClickHouse day numbers", () => {
    expect(day("2026-07-21")).toBe("2026-07-21");
    expect(day("2026-07-21 23:59:59")).toBe("2026-07-21");
    expect(day("2026-07-21T00:00:00Z")).toBe("2026-07-21");
    expect(day(0)).toBe("1970-01-01");
    expect(day(19723)).toBe("2024-01-01");
    expect(day("20720")).toBe("2026-09-24");
    expect(day(null)).toBe("");
    expect(day("not a date")).toBe("");
    expect(day("2026-13-01")).toBe("");
    expect(day(true)).toBe("");
  });

  it("civilFromDays agrees with the UTC calendar across leap years and eras", () => {
    for (const days of [-719468, -1, 0, 59, 60, 10957, 11016, 19782, 20720, 50000]) {
      const iso = new Date(days * 86_400_000).toISOString().slice(0, 10);
      const civil = civilFromDays(days);
      const text = `${String(civil.year).padStart(4, "0")}-${String(civil.month).padStart(2, "0")}-${String(civil.day).padStart(2, "0")}`;
      if (days >= 0) expect(text, String(days)).toBe(iso);
    }
    expect(civilFromDays(0)).toEqual({ year: 1970, month: 1, day: 1 });
  });

  it("bucket() is the month start, from any accepted day shape or 'YYYY-MM'", () => {
    expect(bucket("2026-07-21")).toBe("2026-07-01");
    expect(bucket("2026-07")).toBe("2026-07-01");
    expect(bucket(20720)).toBe("2026-09-01");
    expect(bucket("2025-12-31 23:00:00")).toBe("2025-12-01");
    expect(bucket("")).toBe("");
  });

  it("flag() reads 0/1, strings and booleans", () => {
    for (const truthy of [1, "1", true, "true", "TRUE"]) expect(flag(truthy)).toBe(true);
    for (const falsy of [0, "0", false, "false", null, undefined, ""]) expect(flag(falsy)).toBe(false);
  });

  it("an unknown or empty class is 'unverified', never promoted to priced", () => {
    expect(tokenClassOf("priced")).toBe("priced");
    expect(tokenClassOf("")).toBe("unverified");
    expect(tokenClassOf("PRICED")).toBe("unverified");
    expect(tokenClassOf(null)).toBe("unverified");
  });
});

describe("parsers", () => {
  const holdings = parseHoldings(asRows(MOCK_PAYLOAD.datasets!.treasury_holdings));

  it("reads by column name, so column order never matters", () => {
    const ds = asRows(MOCK_PAYLOAD.datasets!.treasury_summary)!;
    const reversed: RowDataset = {
      columns: [...ds.columns].reverse(),
      rows: ds.rows.map((row) => [...row].reverse()),
    };
    expect(parseSummary(reversed)).toEqual(parseSummary(ds));
  });

  it("lowercases addresses and keeps NULL as null (never 0)", () => {
    const parsed = parseHoldings({
      columns: ["chain_id", "token_address", "symbol", "balance_units", "value_usd", "value_usd_ex_ltd"],
      rows: [[1, "0xAbCdEf0000000000000000000000000000000001", "AAA", null, null, null]],
    });
    expect(parsed[0].token).toBe("0xabcdef0000000000000000000000000000000001");
    expect(parsed[0].units).toBeNull();
    expect(parsed[0].valueUsd).toBeNull();
    expect(parsed[0].hasExLtd).toBe(true);
  });

  it("drops rows without an address or chain — the address is the identity", () => {
    const parsed = parseHoldings({
      columns: ["chain_id", "token_address"],
      rows: [[1, ""], [null, "0x01"], [1, "0x02"]],
    });
    expect(parsed.map((row) => row.token)).toEqual(["0x02"]);
  });

  it("sanitizes on-chain text: the obfuscated U+034F spoof reads as plain text, never with the joiner", () => {
    const obfuscated = holdings.find((row) => row.token === T.OBF_1)!;
    expect(obfuscated.symbol).toBe("USDC");
    expect(obfuscated.symbol).not.toContain("\u{034F}");
    const raid = holdings.find((row) => row.token === T.RAID_1)!;
    // VS16 stripped, the emoji base kept.
    expect(raid.symbol).toBe("RAID \u{2694}");
    const malformed = parseHoldings(asRows(MOCK_PAYLOAD.datasets!.treasury_holdings))
      .find((row) => row.token === T.MALFORMED_100)!;
    expect(malformed.symbol).toBe("");
  });

  it("caps and sanitizes wallet labels at 40 code points", () => {
    const [wallet] = parseWallets({
      columns: ["chain_id", "wallet_address", "wallet_label"],
      rows: [[1, "0x0000000000000000000000000000000000000001", `${"x".repeat(60)}\u{202E}`]],
    });
    expect(Array.from(wallet.label).length).toBeLessThanOrEqual(40);
    expect(wallet.label.endsWith("…")).toBe(true);
    expect(wallet.label).not.toContain("\u{202E}");
  });

  it("parses every fixture dataset without losing rows", () => {
    const ds = MOCK_PAYLOAD.datasets!;
    expect(parseSummary(asRows(ds.treasury_summary))).toHaveLength(2);
    expect(holdings.length).toBe(ds.treasury_holdings.preview_rows.length);
    expect(parseWallets(asRows(ds.treasury_by_wallet)).length).toBe(ds.treasury_by_wallet.preview_rows.length);
    expect(parseHistory(asRows(ds.treasury_history)).length).toBe(ds.treasury_history.preview_rows.length);
    expect(parseCoverage(asRows(ds.treasury_history_coverage)).length)
      .toBe(ds.treasury_history_coverage.preview_rows.length);
  });

  it("token detail parses sibling tokens and drops a self-reference", () => {
    const datasets = treasuryEntityDatasets("treasury_token", `1:${T.GNO_1}`);
    const detail = parseTokenDetail(asRows(datasets.treasury_token_detail))!;
    expect(detail.registrySymbol).toBe("GNO");
    expect(detail.siblings).toEqual([{ chainId: 100, token: T.GNO_100 }]);
    const withSelf = parseTokenDetail({
      columns: ["chain_id", "token_address", "sibling_tokens"],
      rows: [[1, T.GNO_1, `["1:${T.GNO_1}","100:${T.GNO_100}"]`]],
    })!;
    expect(withSelf.siblings).toEqual([{ chainId: 100, token: T.GNO_100 }]);
  });

  it("coverage accepts registry symbols as an array or a joined string", () => {
    const rows = parseCoverage({
      columns: ["chain_id", "bucket", "status", "unserved_registry_symbols"],
      rows: [
        [1, "2026-07-01", "partial", ["SAFE", "USDC"]],
        [1, "2026-08-01", "complete", "['SAFE','USDC']"],
        [1, "2026-09-01", "weird", ""],
      ],
    });
    expect(rows[0].unservedRegistrySymbols).toEqual(["SAFE", "USDC"]);
    expect(rows[1].unservedRegistrySymbols).toEqual(["SAFE", "USDC"]);
    // An unknown status is a month nobody vouches for: a gap, never complete.
    expect(rows[2].status).toBe("gap");
  });

  it("wallet presence, holders and price history parse from the fixture bundles", () => {
    const wallet = treasuryEntityDatasets("treasury_wallet", `100:${W_GNOSIS_ONLY}`);
    const presence = parseWalletChains(asRows(wallet.treasury_wallet_chains));
    expect(presence.map((row) => [row.chainId, row.tracked])).toEqual([[1, false], [100, true]]);
    const token = treasuryEntityDatasets("treasury_token", `1:${T.GNO_1}`);
    const holders = parseHolders(asRows(token.treasury_token_holders));
    expect(holders.some((holder) => holder.isLtd)).toBe(true);
    expect(holders.find((holder) => holder.wallet === W_MAIN)?.label).toBe("DAO Main Safe");
    const prices = parsePriceHistory(asRows(token.treasury_token_price_history));
    expect(prices.length).toBeGreaterThan(50);
    expect(prices[0].day < prices[prices.length - 1].day).toBe(true);
  });
});

describe("fixture totals agree across datasets", () => {
  const ds = MOCK_PAYLOAD.datasets!;
  const summary = parseSummary(asRows(ds.treasury_summary));
  const holdings = parseHoldings(asRows(ds.treasury_holdings));
  const history = parseHistory(asRows(ds.treasury_history));
  const wallets = parseWallets(asRows(ds.treasury_by_wallet));

  it("summary NAV = sum of holdings value = chain grain at the as-of month = token grain = wallet grain", () => {
    for (const row of summary) {
      const hub = holdings.filter((h) => h.chainId === row.chainId).reduce((acc, h) => acc + (h.valueUsd ?? 0), 0);
      expect(hub).toBeCloseTo(row.navUsd ?? Number.NaN, 4);
      const chainGrain = history.find((h) => h.grain === "chain" && h.chainId === row.chainId && h.bucket === "2026-09-01")!;
      expect(chainGrain.navUsd).toBeCloseTo(row.navUsd ?? Number.NaN, 4);
      const tokenSum = history.filter((h) => h.grain === "token" && h.chainId === row.chainId && h.bucket === "2026-09-01")
        .reduce((acc, h) => acc + (h.navUsd ?? 0), 0);
      expect(tokenSum).toBeCloseTo(row.navUsd ?? Number.NaN, 4);
      const walletSum = history.filter((h) => h.grain === "wallet" && h.chainId === row.chainId && h.bucket === "2026-09-01")
        .reduce((acc, h) => acc + (h.navUsd ?? 0), 0);
      expect(walletSum).toBeCloseTo(row.navUsd ?? Number.NaN, 4);
      const byWallet = wallets.filter((w) => w.chainId === row.chainId).reduce((acc, w) => acc + (w.navUsd ?? 0), 0);
      expect(byWallet).toBeCloseTo(row.navUsd ?? Number.NaN, 4);
    }
  });

  it("the ex-Ltd companions agree too", () => {
    for (const row of summary) {
      const hubEx = holdings.filter((h) => h.chainId === row.chainId).reduce((acc, h) => acc + (h.valueUsdExLtd ?? 0), 0);
      expect(hubEx).toBeCloseTo(row.navUsdExLtd ?? Number.NaN, 4);
      const nonLtd = history.filter((h) => h.grain === "wallet" && h.chainId === row.chainId && h.bucket === "2026-09-01" && !h.isLtd)
        .reduce((acc, h) => acc + (h.navUsd ?? 0), 0);
      expect(nonLtd).toBeCloseTo(row.navUsdExLtd ?? Number.NaN, 4);
    }
  });

  it("covers every class and every spam reason", () => {
    expect(new Set(holdings.map((h) => h.tokenClass))).toEqual(new Set(TOKEN_CLASSES));
    const reasons = new Set(holdings.filter((h) => h.tokenClass === "spam").map((h) => h.spamReason));
    expect(reasons).toEqual(new Set(SPAM_REASONS.filter((reason) => reason !== "")));
  });

  it("mirrors the live shape: 146 coverage months, hub_proxy pricing, listed-only spot, not-held tokens", () => {
    const coverage = parseCoverage(asRows(ds.treasury_history_coverage));
    expect(coverage).toHaveLength(146);
    expect(coverage.filter((row) => row.chainId === 1)).toHaveLength(71);
    expect(coverage.filter((row) => row.chainId === 100)).toHaveLength(75);
    expect(holdings.find((h) => h.token === T.STETH_1)).toMatchObject({ tokenClass: "priced", priceSource: "hub_proxy" });
    expect(holdings.filter((h) => h.spotEligible).every((h) => h.tokenClass === "listed")).toBe(true);
    expect(holdings.find((h) => h.token === T.RAID_1)?.spotEligible).toBe(false);
    const sold = treasuryEntityDatasets("treasury_token", `1:${T.BAL_1}`);
    expect(sold.treasury_token_detail.preview_rows).toHaveLength(0);
    expect(sold.treasury_token_holder_series.preview_rows.length).toBeGreaterThan(0);
    expect(sold.treasury_token_price_history.preview_rows.length).toBeGreaterThan(0);
    // Magnitudes near the live 2026-09-24 figures.
    expect((summary[0].navUsd ?? 0) / 1e6).toBeCloseTo(115.6, 0);
    expect((summary[0].navUsdExLtd ?? 0) / 1e6).toBeCloseTo(74.5, 0);
    expect((summary[1].navUsd ?? 0) / 1e6).toBeCloseTo(128.3, 0);
  });

  it("the mass airdrop is held by all 23 Ethereum wallets; 47 wallet-chain pairs in all", () => {
    const drop = holdings.find((h) => h.token === T.DROP_1)!;
    expect(drop.walletsHolding).toBe(23);
    expect(wallets).toHaveLength(47);
    expect(wallets.filter((w) => w.wallet === W_GNOSIS_ONLY).map((w) => w.chainId)).toEqual([100]);
    expect(wallets.filter((w) => w.isLtd).map((w) => w.chainId).sort((a, b) => a - b)).toEqual([1, 100]);
  });

  it("mirrors the SQL's edges: label_source on every row, the chain's as_of off the label list, a zero detail row off-census", () => {
    // treasury_by_wallet.sql: {label_source} is a constant; an unlabelled
    // wallet arrives through the FULL JOIN's data side and takes its chain's
    // as_of from its own rows — every row carries a date, never NULL.
    const unlabelled = wallets.find((w) => w.wallet === W_GNOSIS_ONLY)!;
    expect(unlabelled).toMatchObject({ label: "", asOf: "2026-09-24" });
    expect(unlabelled.labelSource).not.toBe("");
    expect(wallets.every((w) => w.asOf === "2026-09-24")).toBe(true);
    // wallet_detail.sql: one row per served chain for ANY address, zeros
    // where it holds nothing.
    const off = parseWallets(asRows(treasuryEntityDatasets("treasury_wallet", `1:${W_GNOSIS_ONLY}`).treasury_wallet_detail));
    expect(off).toHaveLength(1);
    expect(off[0]).toMatchObject({ chainId: 1, wallet: W_GNOSIS_ONLY, label: "", tokensHeld: 0, pricedPositions: 0, navUsd: 0 });
  });
});
