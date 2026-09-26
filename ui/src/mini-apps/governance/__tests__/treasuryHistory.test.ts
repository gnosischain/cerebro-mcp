// Treasury history transforms: the month spine, month statuses (a gap is a
// gap, never a dip), the per-mode fact adapters, and stacking (only keys that
// carry value may take a band; the rest fold into "Other (+k)").

import { describe, expect, it } from "vitest";

import { MOCK_PAYLOAD } from "../devFixture";
import { T, W_LTD, W_MAIN } from "../devFixtureTreasury";
import {
  addMonths,
  breadthSeries,
  chainDataBuckets,
  combineStatuses,
  factBuckets,
  firstPricedMarkers,
  historyFacts,
  historyFrame,
  holderSeriesFacts,
  measureAllowed,
  monthSpine,
  seriesSparkIndex,
  sparkIndex,
  stackFacts,
  walletSeriesFacts,
  windowFrame,
  windowSpine,
  type Fact,
  type HistoryFrame,
  type MonthStatus,
} from "../model/treasuryHistory";
import { parseCoverage, parseHistory, type CoverageRow, type HistoryRow } from "../model/treasuryRows";

const rows = (key: string) => ({
  columns: MOCK_PAYLOAD.datasets![key].columns.map((column) => column.name),
  rows: MOCK_PAYLOAD.datasets![key].preview_rows,
});
const HISTORY = parseHistory(rows("treasury_history"));
const COVERAGE = parseCoverage(rows("treasury_history_coverage"));

function frameFor(chains: number[], coverage: CoverageRow[] | null = COVERAGE): HistoryFrame {
  return historyFrame({ chains, coverage, dataBuckets: chainDataBuckets(HISTORY) });
}

function cov(chainId: number, bucket: string, status: CoverageRow["status"]): CoverageRow {
  return {
    chainId, bucket, rawMonthEnd: "", bucketDate: "", publishedTokens: null, servedTokens: null,
    carriedTokens: null, unservedTokens: null, unservedRegistryTokens: null, unservedRegistrySymbols: [], status,
  };
}

function fact(bucket: string, key: string, value: number | null): Fact {
  return { bucket, key, label: key.toUpperCase(), value };
}

describe("month spine", () => {
  it("walks months across a year boundary", () => {
    expect(monthSpine("2025-11-01", "2026-02-01")).toEqual(["2025-11-01", "2025-12-01", "2026-01-01", "2026-02-01"]);
    expect(monthSpine("2026-02-01", "2025-11-01")).toEqual([]);
    expect(monthSpine("junk", "2026-01-01")).toEqual([]);
    expect(addMonths("2025-12-01", 1)).toBe("2026-01-01");
    expect(addMonths("2026-01-01", -1)).toBe("2025-12-01");
    expect(addMonths("2020-02-01", 70)).toBe("2025-12-01");
  });

  it("windows keep the last 12 / 36 months, or everything", () => {
    const spine = monthSpine("2020-11-01", "2026-09-01");
    expect(spine).toHaveLength(71);
    expect(windowSpine(spine, "1y")).toHaveLength(12);
    expect(windowSpine(spine, "1y")[0]).toBe("2025-10-01");
    expect(windowSpine(spine, "3y")).toHaveLength(36);
    expect(windowSpine(spine, "all")).toBe(spine);
    expect(windowSpine(spine.slice(0, 5), "3y")).toHaveLength(5);
  });
});

describe("month statuses", () => {
  it("combines chains: not-yet-started chains are ignored; any missing chain blanks the month, any partial one marks it", () => {
    expect(combineStatuses(["before", "ok"])).toBe("ok");
    expect(combineStatuses(["before", "before"])).toBe("before");
    expect(combineStatuses(["ok", "incomplete"])).toBe("incomplete");
    expect(combineStatuses(["incomplete", "missing"])).toBe("missing");
  });

  it("marks the fixture's partial and blank months, and nothing else", () => {
    const frame = frameFor([1, 100]);
    // The full history: Gnosis Chain from 2020-07 (75 months), Ethereum from 2020-11.
    expect(frame.buckets[0]).toBe("2020-07-01");
    expect(frame.buckets).toHaveLength(75);
    expect(frame.buckets[frame.buckets.length - 1]).toBe("2026-09-01");
    const at = (bucket: string) => frame.statuses[frame.buckets.indexOf(bucket)];
    // 'partial' is DRAWN (incomplete); 'gap' and 'unpublished' are blanked.
    expect(at("2026-07-01")).toBe("incomplete");
    expect(at("2025-02-01")).toBe("missing");
    expect(at("2023-02-01")).toBe("missing");
    // Before Ethereum starts (2020-07..10), Gnosis Chain alone decides.
    expect(at("2020-08-01")).toBe("ok");
    expect(frame.statuses.filter((status) => status !== "ok")).toHaveLength(3);
    expect(frame.firstByChain.get(1)).toBe("2020-11-01");
    expect(frame.firstByChain.get(100)).toBe("2020-07-01");
    const eth = frame.issuesByChain.get(1)!;
    expect(eth.map((issue) => [issue.bucket, issue.status, issue.coverage?.status])).toEqual([
      ["2023-02-01", "missing", "unpublished"],
      ["2026-07-01", "incomplete", "partial"],
    ]);
    expect(eth[1].coverage?.unservedRegistrySymbols).toEqual(["SAFE"]);
    expect(frame.issuesByChain.get(100)!.map((issue) => [issue.bucket, issue.coverage?.status]))
      .toEqual([["2025-02-01", "gap"]]);
  });

  it("a single-chain frame starts at that chain's first month", () => {
    const eth = frameFor([1]);
    expect(eth.buckets[0]).toBe("2020-11-01");
    expect(eth.buckets).toHaveLength(71);
    const gnosis = frameFor([100]);
    expect(gnosis.statuses.filter((status) => status === "missing")).toHaveLength(1);
    expect(gnosis.statuses.includes("incomplete")).toBe(false);
  });

  it("without coverage, months with data are ok and months without are gaps", () => {
    const frame = historyFrame({
      chains: [1],
      coverage: null,
      dataBuckets: new Map([[1, new Set(["2026-01-01", "2026-03-01"])]]),
    });
    expect(frame.buckets).toEqual(["2026-01-01", "2026-02-01", "2026-03-01"]);
    expect(frame.statuses).toEqual<MonthStatus[]>(["ok", "missing", "ok"]);
  });

  it("an entity frame starts at its first DATA month, not the chain's", () => {
    const frame = historyFrame({
      chains: [1],
      coverage: [cov(1, "2026-06-01", "complete"), cov(1, "2026-07-01", "partial"), cov(1, "2026-08-01", "complete"), cov(1, "2026-09-01", "complete")],
      dataBuckets: new Map([[1, new Set(["2026-07-01", "2026-08-01", "2026-09-01"])]]),
      startAtData: true,
    });
    expect(frame.buckets).toEqual(["2026-07-01", "2026-08-01", "2026-09-01"]);
    expect(frame.statuses).toEqual<MonthStatus[]>(["incomplete", "ok", "ok"]);
  });
});

describe("stackFacts", () => {
  const frame = {
    buckets: ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"],
    statuses: ["ok", "missing", "ok", "ok"] as MonthStatus[],
  };

  it("REGRESSION: a key with no value (unpriced / spam) never takes a band", () => {
    const facts = [
      fact("2026-01-01", "gno", 100), fact("2026-03-01", "gno", 120),
      fact("2026-01-01", "aaa-unpriced", null), fact("2026-03-01", "aaa-unpriced", null),
      fact("2026-01-01", "abc-zero", 0),
      fact("2026-03-01", "cow", 5),
    ];
    const stack = stackFacts(facts, frame, { prefix: "asset", maxBands: 5 });
    expect(stack.bands.map((band) => band.key)).toEqual(["gno", "cow"]);
    expect(stack.bands.some((band) => band.label.includes("AAA"))).toBe(false);
  });

  it("folds everything past the ceiling into 'Other (+k)' and discloses k", () => {
    const facts = Array.from({ length: 8 }, (_, index) => fact("2026-01-01", `k${index}`, 100 - index));
    const stack = stackFacts(facts, frame, { prefix: "asset", maxBands: 5 });
    expect(stack.bands).toHaveLength(5);
    expect(stack.bands.map((band) => band.key)).toEqual(["k0", "k1", "k2", "k3", "other"]);
    expect(stack.bands[4]).toMatchObject({ id: "other", label: "Other (+4)", isOther: true });
    expect(stack.bands[4].folded).toEqual(["k4", "k5", "k6", "k7"]);
    expect(stack.bands[4].data[0]).toBe(96 + 95 + 94 + 93);
  });

  it("nulls EVERY band in a gap month and fills 0 for not-held in a served month", () => {
    const facts = [fact("2026-01-01", "gno", 100), fact("2026-02-01", "gno", 999), fact("2026-04-01", "cow", 7)];
    const stack = stackFacts(facts, frame, { prefix: "asset" });
    const gno = stack.bands.find((band) => band.key === "gno")!;
    const cow = stack.bands.find((band) => band.key === "cow")!;
    expect(gno.data).toEqual([100, null, 0, 0]);
    expect(cow.data).toEqual([0, null, 0, 7]);
    expect(stack.totals).toEqual([100, null, 0, 7]);
    expect(stack.gaps).toEqual(["2026-02-01"]);
    expect(stack.partial).toEqual([]);
  });

  it("DRAWS a partial month (from what was served) and lists it — never blanks it", () => {
    const partialFrame = { buckets: frame.buckets, statuses: ["ok", "incomplete", "ok", "ok"] as MonthStatus[] };
    const stack = stackFacts([fact("2026-01-01", "gno", 100), fact("2026-02-01", "gno", 90)], partialFrame, { prefix: "asset" });
    expect(stack.bands[0].data).toEqual([100, 90, 0, 0]);
    expect(stack.gaps).toEqual([]);
    expect(stack.partial).toEqual(["2026-02-01"]);
  });

  it("ranks on the window: a key only valued in a gap month gets no band", () => {
    const stack = stackFacts([fact("2026-02-01", "ghost", 1e9), fact("2026-01-01", "gno", 1)], frame, { prefix: "asset" });
    expect(stack.bands.map((band) => band.key)).toEqual(["gno"]);
  });

  it("series ids carry the mode prefix so a click resolves its entity", () => {
    const stack = stackFacts([fact("2026-01-01", "0xabc", 1)], frame, { prefix: "wallet" });
    expect(stack.bands[0].id).toBe("wallet:0xabc");
  });
});

describe("fact adapters over the fixture", () => {
  const frame = frameFor([1, 100]);
  const okIndexes = frame.statuses.map((status, index) => (status === "ok" ? index : -1)).filter((index) => index >= 0);

  it("totals match across every USD mode, month by month", () => {
    const totals = (["chain", "asset", "wallet", "class"] as const).map((mode) => (
      stackFacts(historyFacts(HISTORY, { mode, measure: "usd", chain: 0, exLtd: false }), frame, { prefix: mode }).totals
    ));
    for (const index of okIndexes) {
      for (const series of totals.slice(1)) expect(series[index]).toBeCloseTo(totals[0][index] ?? Number.NaN, 4);
    }
  });

  it("ex-Ltd totals match across modes too, and are smaller", () => {
    const all = stackFacts(historyFacts(HISTORY, { mode: "chain", measure: "usd", chain: 0, exLtd: false }), frame, { prefix: "chain" }).totals;
    const byChain = stackFacts(historyFacts(HISTORY, { mode: "chain", measure: "usd", chain: 0, exLtd: true }), frame, { prefix: "chain" }).totals;
    const byWallet = stackFacts(historyFacts(HISTORY, { mode: "wallet", measure: "usd", chain: 0, exLtd: true }), frame, { prefix: "wallet" }).totals;
    const byAsset = stackFacts(historyFacts(HISTORY, { mode: "asset", measure: "usd", chain: 0, exLtd: true }), frame, { prefix: "asset" }).totals;
    for (const index of okIndexes) {
      expect(byWallet[index]).toBeCloseTo(byChain[index] ?? Number.NaN, 4);
      expect(byAsset[index]).toBeCloseTo(byChain[index] ?? Number.NaN, 4);
      expect((byChain[index] ?? 0) < (all[index] ?? 0)).toBe(true);
    }
  });

  it("GNO units agree between the chain and wallet stacks", () => {
    const byChain = stackFacts(historyFacts(HISTORY, { mode: "chain", measure: "gno", chain: 0, exLtd: false }), frame, { prefix: "chain" }).totals;
    const byWallet = stackFacts(historyFacts(HISTORY, { mode: "wallet", measure: "gno", chain: 0, exLtd: false }), frame, { prefix: "wallet" }).totals;
    for (const index of okIndexes) expect(byWallet[index]).toBeCloseTo(byChain[index] ?? Number.NaN, 4);
  });

  it("the chain filter keeps only that chain's rows", () => {
    const facts = historyFacts(HISTORY, { mode: "chain", measure: "usd", chain: 100, exLtd: false });
    expect(new Set(facts.map((entry) => entry.key))).toEqual(new Set(["100"]));
  });

  it("ex-Ltd drops the Ltd wallet at wallet grain and colours it otherwise", () => {
    const withLtd = historyFacts(HISTORY, { mode: "wallet", measure: "usd", chain: 0, exLtd: false });
    expect(withLtd.find((entry) => entry.key === W_LTD)?.color).toBe("#F5B14C");
    const without = historyFacts(HISTORY, { mode: "wallet", measure: "usd", chain: 0, exLtd: true });
    expect(without.some((entry) => entry.key === W_LTD)).toBe(false);
  });

  it("asset mode merges chains by asset key and labels by the trusted symbol", () => {
    const facts = historyFacts(HISTORY, { mode: "asset", measure: "usd", chain: 0, exLtd: false });
    const keys = new Set(facts.map((entry) => entry.key));
    expect(keys.has("GNO")).toBe(true);
    expect(facts.find((entry) => entry.key === "USDC")?.label).toBe("USDC");
    expect(facts.find((entry) => entry.key === "ETH")?.label).toBe("WETH");
    // Spam and unverified tokens are not in the token grain at all.
    expect([...keys].some((key) => key.includes(T.FAKE_USDC_1))).toBe(false);
  });

  it("GNO units are refused for asset/class stacks (they would add GNO to stablecoins)", () => {
    expect(measureAllowed("asset", "gno")).toBe(false);
    expect(measureAllowed("class", "gno")).toBe(false);
    expect(measureAllowed("chain", "gno")).toBe(true);
    expect(measureAllowed("wallet", "gno")).toBe(true);
    const facts = historyFacts(HISTORY, { mode: "asset", measure: "gno", chain: 0, exLtd: false });
    const usd = historyFacts(HISTORY, { mode: "asset", measure: "usd", chain: 0, exLtd: false });
    expect(facts).toEqual(usd);
  });

  it("blank months are null in the stacked output; the partial month is drawn", () => {
    const stack = stackFacts(historyFacts(HISTORY, { mode: "asset", measure: "usd", chain: 0, exLtd: false }), frame, { prefix: "asset" });
    for (const bucket of ["2023-02-01", "2025-02-01"]) {
      const index = stack.buckets.indexOf(bucket);
      for (const band of stack.bands) expect(band.data[index]).toBeNull();
    }
    expect(stack.gaps).toEqual(["2023-02-01", "2025-02-01"]);
    expect(stack.partial).toEqual(["2026-07-01"]);
    const partialIndex = stack.buckets.indexOf("2026-07-01");
    expect(stack.totals[partialIndex]).toBeGreaterThan(0);
  });

  it("range windows slice the frame", () => {
    expect(windowFrame(frame, "1y").buckets).toHaveLength(12);
    expect(windowFrame(frame, "1y").statuses).toHaveLength(12);
    expect(windowFrame(frame, "all")).toBe(frame);
  });
});

describe("breadth, sparklines, first-priced markers", () => {
  const frame = frameFor([1, 100]);

  it("breadth counts are null in blank months and spam appears only when shown", () => {
    const hidden = breadthSeries(HISTORY, frame, { chain: 0, showHidden: false });
    expect(hidden.spam).toBeNull();
    const gapIndex = frame.buckets.indexOf("2025-02-01");
    expect(hidden.priced[gapIndex]).toBeNull();
    expect(hidden.priced[frame.buckets.indexOf("2026-07-01")]).toBeGreaterThan(0);
    const shown = breadthSeries(HISTORY, frame, { chain: 0, showHidden: true });
    expect(shown.spam?.[frame.buckets.length - 1]).toBe(5);
    expect(shown.priced[frame.buckets.length - 1]).toBeGreaterThan(0);
  });

  it("sparklines cover 24 months, break (NaN) in gaps, and exist only for valued keys", () => {
    const spark = sparkIndex(HISTORY, frame, { chain: 0, exLtd: false });
    const gno = spark.get("asset:GNO")!;
    expect(gno).toHaveLength(24);
    const gapIndex = frame.buckets.slice(-24).indexOf("2025-02-01");
    expect(Number.isNaN(gno[gapIndex])).toBe(true);
    expect(spark.get(`1:${T.GNO_1}`)).toBeDefined();
    expect(spark.has(`1:${T.FAKE_USDC_1}`)).toBe(false);
  });

  it("marks where a hub price starts mid-history — only for assets held (as 'listed') before it", () => {
    const markers = firstPricedMarkers(HISTORY, frame, { chain: 0 });
    const byKey = Object.fromEntries(markers.map((marker) => [marker.key, marker.bucket]));
    expect(byKey).toEqual({ COW: "2022-03-01", SAFE: "2024-04-01", OSGNO: "2024-10-01" });
    // Acquisitions are never labelled "priced": BAL (bought already priced),
    // stETH, EURe v2 (the v1 mirror successor) and GNO get no marker.
  });

  it("never marks an acquisition that coincides with nothing leaving 'listed'", () => {
    const row = (overrides: Partial<HistoryRow>): HistoryRow => ({
      grain: "token", chainId: 1, bucket: "2026-01-01", bucketDate: "", wallet: "", walletLabel: "", isLtd: false,
      token: "0xaaa", registrySymbol: "AAA", assetKey: "AAA", assetClass: "Other", tokenClass: "priced",
      units: 1, unitsExLtd: 1, priceUsd: 1, priceDate: "", navUsd: 1, navUsdExLtd: 1, gnoUnits: 0, gnoUnitsExLtd: 0,
      walletsHolding: 1, tokensHeld: 1, positions: 1, pricedTokens: 1, listedTokens: 0, unverifiedTokens: 0,
      hiddenSpamTokens: 0, ...overrides,
    });
    const chainRow = (bucket: string, listed: number) => row({ grain: "chain", bucket, token: "", listedTokens: listed });
    const buckets = ["2025-11-01", "2025-12-01", "2026-01-01", "2026-02-01"];
    const rows = [
      chainRow("2025-11-01", 1), chainRow("2025-12-01", 1), chainRow("2026-01-01", 1), chainRow("2026-02-01", 0),
      row({ bucket: "2025-11-01", token: "0xold" }),
      // Bought in 2026-01 while the listed count stayed flat: an acquisition.
      row({ bucket: "2026-01-01", token: "0xnew", assetKey: "NEW" }),
      // First priced in 2026-02 as one token left 'listed': a marker.
      row({ bucket: "2026-02-01", token: "0xlisted", assetKey: "LST" }),
    ];
    const markers = firstPricedMarkers(rows, { buckets }, { chain: 0 });
    expect(markers.map((marker) => marker.key)).toEqual(["LST"]);
  });

  it("entity series adapters", () => {
    const series = walletSeriesFacts([
      { chainId: 1, bucket: "2026-08-01", bucketDate: "", token: T.GNO_1, registrySymbol: "GNO", assetKey: "GNO", assetClass: "GNO", tokenClass: "priced", units: 1, priceUsd: 2, priceDate: "", valueUsd: 2 },
      { chainId: 1, bucket: "2026-08-01", bucketDate: "", token: T.RAID_1, registrySymbol: "", assetKey: "", assetClass: "", tokenClass: "unverified", units: 1, priceUsd: null, priceDate: "", valueUsd: null },
    ]);
    expect(series).toEqual([{ bucket: "2026-08-01", key: "GNO", label: "GNO", value: 2 }]);
    const holders = holderSeriesFacts([
      { chainId: 1, bucket: "2026-08-01", bucketDate: "", wallet: W_MAIN, label: "DAO Main Safe", isLtd: false, units: 5, valueUsd: 10 },
      { chainId: 1, bucket: "2026-08-01", bucketDate: "", wallet: W_LTD, label: "Gnosis Ltd.", isLtd: true, units: 3, valueUsd: 6 },
    ], { measure: "units", exLtd: true });
    expect(holders).toHaveLength(1);
    expect(holders[0].value).toBe(5);
    expect(factBuckets(holders, 1).get(1)).toEqual(new Set(["2026-08-01"]));
    const spark = seriesSparkIndex(
      [{ bucket: "2026-08-01", chainId: 1, token: "0xabc", value: 4 }],
      { buckets: ["2026-07-01", "2026-08-01"], statuses: ["missing", "ok"] },
    );
    expect(Number.isNaN(spark.get("1:0xabc")![0])).toBe(true);
    expect(spark.get("1:0xabc")![1]).toBe(4);
  });
});

describe("the 0x458c wallet on Ethereum", () => {
  it("history starts in the partial 2026-07 month — drawn, not blanked — then two complete months", () => {
    const rowsFor = HISTORY.filter((row: HistoryRow) => row.grain === "wallet" && row.wallet === W_MAIN && row.chainId === 1);
    expect(rowsFor.map((row) => row.bucket)).toEqual(["2026-07-01", "2026-08-01", "2026-09-01"]);
    const coverage = COVERAGE.filter((row) => row.chainId === 1 && row.bucket >= "2026-07-01");
    const frame = historyFrame({
      chains: [1],
      coverage,
      dataBuckets: new Map([[1, new Set(rowsFor.map((row) => row.bucket))]]),
      startAtData: true,
    });
    expect(frame.statuses).toEqual<MonthStatus[]>(["incomplete", "ok", "ok"]);
  });
});
