// The asset table: spam exists in the DOM only when hidden tokens are shown,
// untrusted symbols always carry their address, unpriced reads "unpriced"
// (never $0), and there is no silent cap. Interactions are covered through
// the table's pure helpers (no testing-library in this repo).

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  AssetTable,
  classification,
  DEFAULT_ASSET_LIMIT,
  filterAssets,
  filterCounts,
  sortAssets,
} from "../components/treasury/AssetTable";
import { MOCK_PAYLOAD } from "../devFixture";
import { T, TREASURY_PRICE_OVERLAY } from "../devFixtureTreasury";
import { parseHoldings, type HoldingRow } from "../model/treasuryRows";
import { assetRows, spotSourceFrom, valuationMap } from "../model/treasuryValue";
import type { AssetFilter } from "../state/treasuryView";

const HOLDINGS = parseHoldings({
  columns: MOCK_PAYLOAD.datasets!.treasury_holdings.columns.map((column) => column.name),
  rows: MOCK_PAYLOAD.datasets!.treasury_holdings.preview_rows,
});
const SPOT = spotSourceFrom(TREASURY_PRICE_OVERLAY, "2026-09-25T08:00:00Z");
const VALUATIONS = valuationMap(HOLDINGS, SPOT, false);
const MERGED = assetRows(HOLDINGS, VALUATIONS, { merge: true });
const BY_CHAIN = assetRows(HOLDINGS, VALUATIONS, { merge: false });

function render(opts: { showHidden?: boolean; filter?: AssetFilter; merged?: typeof MERGED; limit?: number } = {}) {
  return renderToStaticMarkup(
    <AssetTable
      merged={opts.merged ?? MERGED}
      byChain={opts.merged ?? BY_CHAIN}
      mergeAvailable
      filter={opts.filter ?? "all"}
      onFilter={() => {}}
      showHidden={opts.showHidden ?? false}
      hiddenCount={HOLDINGS.filter((row) => row.tokenClass === "spam").length}
      onOpen={() => {}}
      iconFor={() => ""}
      spotAt="2026-09-25T08:00:00Z"
      totalUsd={1_000_000}
      limit={opts.limit}
    />,
  );
}

describe("AssetTable", () => {
  it("never renders spam while hidden tokens are off — not even its address", () => {
    const out = render();
    expect(out).not.toContain(T.FAKE_USDC_1.slice(0, 6));
    expect(out).not.toContain("aave-sr.xyz");
    expect(out).not.toContain("ZKDROP");
    expect(out).toContain("5 hidden tokens are not listed");
  });

  it("shows spam, with its reason, once hidden tokens are on", () => {
    const out = render({ showHidden: true, filter: "hidden" });
    expect(out).toContain("impersonation");
    expect(out).toContain("mass airdrop");
    expect(out).toContain("Hidden (5)");
  });

  it("untrusted symbols always carry their address; registry symbols stand alone", () => {
    const out = render();
    // RAID is unverified: its symbol renders with the short address.
    expect(out).toMatch(/RAID[^<]*<span class="ma-token__disambig"/);
    expect(out).toContain("ma-token__label\">GNO<");
  });

  it("an unpriced value reads 'unpriced', never $0; spot values carry a spot chip", () => {
    const out = render();
    expect(out).toContain("unpriced");
    expect(out).not.toMatch(/>\$0\.00</);
    expect(out).toContain("gov-trs-chip--spot");
  });

  it("merges registry assets across chains into one row with a chip per chain", () => {
    const gno = MERGED.find((row) => row.key === "asset:GNO")!;
    expect(gno.members.map((member) => member.holding.chainId).sort((a, b) => a - b)).toEqual([1, 100]);
    const out = render();
    expect(out).toContain("gov-trs-chainchip");
  });

  it("caps at 50 rows with a 'Show all N' — never silently", () => {
    const many = Array.from({ length: 60 }, (_, index): HoldingRow => ({
      ...HOLDINGS[0],
      key: `1:0x${String(index).padStart(40, "0")}`,
      token: `0x${String(index).padStart(40, "0")}`,
      registrySymbol: `T${index}`,
      assetKey: `T${index}`,
      valueUsd: 1000 - index,
    }));
    const rows = assetRows(many, valuationMap(many, null, false), { merge: true });
    const out = render({ merged: rows });
    expect(DEFAULT_ASSET_LIMIT).toBe(50);
    expect((out.match(/class="gov-trs-row"/g) ?? []).length).toBe(50);
    expect(out).toContain("Showing 50 of 60 assets.");
    expect(out).toContain("Show all 60");
  });

  it("says when everything is shown", () => {
    expect(render()).toMatch(/All \d+ assets shown\./);
  });
});

describe("pure helpers", () => {
  it("the class chips filter on the valuation kind; spam never passes unless shown", () => {
    const hub = filterAssets(MERGED, { filter: "hub", showHidden: true, query: "" });
    expect(hub.every((row) => row.kind === "hub" || row.kind === "mixed")).toBe(true);
    const spot = filterAssets(MERGED, { filter: "spot", showHidden: false, query: "" });
    // Listed tokens only; stETH is hub-priced through WETH (peg proxy).
    expect(spot.map((row) => row.label).sort()).toEqual(["GRT", "xBZZ"]);
    expect(MERGED.find((row) => row.label === "stETH")).toMatchObject({ kind: "hub", proxy: true });
    const unpriced = filterAssets(MERGED, { filter: "unpriced", showHidden: false, query: "" });
    expect(unpriced.some((row) => row.label === "LDO")).toBe(true);
    expect(filterAssets(MERGED, { filter: "hidden", showHidden: false, query: "" })).toEqual([]);
    expect(filterAssets(MERGED, { filter: "retired", showHidden: false, query: "" }).map((row) => row.label)).toEqual(["EURe v1"]);
  });

  it("search matches symbol, registry label, name and address", () => {
    expect(filterAssets(MERGED, { filter: "all", showHidden: false, query: "gno" }).length).toBeGreaterThan(0);
    expect(filterAssets(MERGED, { filter: "all", showHidden: false, query: T.STETH_1.slice(2, 10) }).map((row) => row.label)).toEqual(["stETH"]);
    // An unverified token is never spot-valued, even though CoinGecko may list it.
    expect(filterAssets(MERGED, { filter: "unpriced", showHidden: false, query: "raid" })).toHaveLength(1);
    // A spam token is not findable while hidden...
    expect(filterAssets(MERGED, { filter: "all", showHidden: false, query: "aave" })).toEqual([]);
    // ...and is when shown.
    expect(filterAssets(MERGED, { filter: "all", showHidden: true, query: "aave" })).toHaveLength(1);
  });

  it("counts per chip over the rows the table could show", () => {
    const hidden = filterCounts(MERGED, false);
    const shown = filterCounts(MERGED, true);
    expect(shown.all - hidden.all).toBe(5);
    expect(hidden.hidden).toBe(0);
    expect(shown.hidden).toBe(5);
  });

  it("sorts by value (nulls last), units, holders or name", () => {
    const byValue = sortAssets(MERGED, "value");
    const firstNull = byValue.findIndex((row) => row.usd === null);
    expect(byValue.slice(firstNull).every((row) => row.usd === null)).toBe(true);
    const byName = sortAssets(MERGED.filter((row) => !row.hidden), "name");
    expect(byName[0].label <= byName[1].label).toBe(true);
    const byHolders = sortAssets(MERGED, "holders");
    expect(byHolders[0].holders).toBe(Math.max(...MERGED.map((row) => row.holders ?? 0)));
  });

  it("classification counts every class and spam reason — counts only", () => {
    const { byClass, byReason } = classification(HOLDINGS);
    expect(byClass.find((entry) => entry.key === "spam")?.count).toBe(5);
    expect(byClass.find((entry) => entry.key === "retired_mirror")?.count).toBe(1);
    expect(byReason.map((entry) => entry.count)).toEqual([1, 1, 1, 1, 1]);
  });
});
