// Client-side treasury view state: defaults, invalid-combination repair, the
// URL round-trip (defaults omitted, old tab ids aliased), the server's
// initial-view hints, and the tab -> group map.

import { describe, expect, it } from "vitest";

import { SECTION_GROUPS } from "../model/datasetGroups";
import {
  DEFAULT_TREASURY_TAB,
  groupsForTab,
  resolveTreasuryTab,
  toTreasuryTab,
  TREASURY_TABS,
} from "../model/treasuryTabs";
import {
  applyTreasuryPatch,
  DEFAULT_TREASURY_VIEW,
  describeTreasuryView,
  initialTreasuryView,
  normalizeTreasuryView,
  TREASURY_URL_KEYS,
  treasuryViewFromParams,
  writeTreasuryParams,
  type TreasuryViewState,
} from "../state/treasuryView";

describe("defaults and normalization", () => {
  it("defaults to the overview, all chains, Ltd. included, spam hidden, full USD history by asset", () => {
    expect(DEFAULT_TREASURY_VIEW).toEqual({
      tab: "overview", chain: 0, exLtd: false, showHidden: false,
      stackBy: "asset", measure: "usd", range: "all", assetFilter: "all",
    });
  });

  it("GNO units only stack by chain or wallet", () => {
    expect(normalizeTreasuryView({ ...DEFAULT_TREASURY_VIEW, stackBy: "asset", measure: "gno" }).measure).toBe("usd");
    expect(normalizeTreasuryView({ ...DEFAULT_TREASURY_VIEW, stackBy: "class", measure: "gno" }).measure).toBe("usd");
    expect(normalizeTreasuryView({ ...DEFAULT_TREASURY_VIEW, stackBy: "wallet", measure: "gno" }).measure).toBe("gno");
  });

  it("the 'hidden' asset filter exists only while hidden tokens are shown", () => {
    expect(normalizeTreasuryView({ ...DEFAULT_TREASURY_VIEW, assetFilter: "hidden" }).assetFilter).toBe("all");
    expect(normalizeTreasuryView({ ...DEFAULT_TREASURY_VIEW, showHidden: true, assetFilter: "hidden" }).assetFilter).toBe("hidden");
  });

  it("the field the user just set wins", () => {
    // Picking GNO while stacked by asset switches the stack to chain...
    expect(applyTreasuryPatch(DEFAULT_TREASURY_VIEW, { measure: "gno" })).toMatchObject({ measure: "gno", stackBy: "chain" });
    // ...and picking an asset stack while measuring GNO goes back to USD.
    const gno: TreasuryViewState = { ...DEFAULT_TREASURY_VIEW, stackBy: "chain", measure: "gno" };
    expect(applyTreasuryPatch(gno, { stackBy: "asset" })).toMatchObject({ measure: "usd", stackBy: "asset" });
    // Hiding spam again drops a "hidden" filter.
    const hidden: TreasuryViewState = { ...DEFAULT_TREASURY_VIEW, showHidden: true, assetFilter: "hidden" };
    expect(applyTreasuryPatch(hidden, { showHidden: false }).assetFilter).toBe("all");
  });
});

describe("URL round-trip", () => {
  it("omits every default, so an ordinary link stays clean", () => {
    const params = new URLSearchParams("token=secret");
    writeTreasuryParams(params, DEFAULT_TREASURY_VIEW);
    expect(params.toString()).toBe("token=secret");
  });

  it("round-trips every non-default key", () => {
    const view: TreasuryViewState = {
      tab: "history", chain: 100, exLtd: true, showHidden: true,
      stackBy: "wallet", measure: "gno", range: "3y", assetFilter: "hidden",
    };
    const params = new URLSearchParams("token=secret&other=1");
    writeTreasuryParams(params, view);
    for (const key of TREASURY_URL_KEYS) expect(params.has(key), key).toBe(true);
    expect(treasuryViewFromParams(params)).toEqual(view);
    expect(params.get("token")).toBe("secret");
    expect(params.get("other")).toBe("1");
  });

  it("deletes stale managed keys before writing", () => {
    const params = new URLSearchParams("tchain=100&tltd=1&ttab=history");
    writeTreasuryParams(params, DEFAULT_TREASURY_VIEW);
    expect(params.toString()).toBe("");
  });

  it("aliases the old tab ids and ignores junk", () => {
    expect(treasuryViewFromParams(new URLSearchParams("ttab=portfolio")).tab).toBe("overview");
    expect(treasuryViewFromParams(new URLSearchParams("ttab=tokens")).tab).toBe("assets");
    expect(treasuryViewFromParams(new URLSearchParams("ttab=nope&tchain=5&tstack=x&trange=2y"))).toEqual({});
    expect(treasuryViewFromParams(new URLSearchParams("tchain=0")).chain).toBe(0);
  });

  it("reports only the keys actually present", () => {
    expect(treasuryViewFromParams(new URLSearchParams("tchain=1"))).toEqual({ chain: 1 });
  });
});

describe("initial view", () => {
  it("seeds chain and Ltd. from the server's initial-view hints", () => {
    expect(initialTreasuryView({}, { chain_id: 100, exclude_ltd: true })).toMatchObject({ chain: 100, exLtd: true });
    expect(initialTreasuryView({}, { chain_id: 0 })).toMatchObject({ chain: 0 });
    expect(initialTreasuryView({}, { chain_id: 42 })).toMatchObject({ chain: 0 });
  });

  it("the URL wins over the hints", () => {
    expect(initialTreasuryView({ chain: 1 }, { chain_id: 100 })).toMatchObject({ chain: 1 });
  });

  it("normalizes a URL that asks for an invalid combination", () => {
    expect(initialTreasuryView({ stackBy: "class", measure: "gno" })).toMatchObject({ stackBy: "class", measure: "usd" });
  });

  it("describes itself for the host model context", () => {
    const text = describeTreasuryView({ ...DEFAULT_TREASURY_VIEW, chain: 100, exLtd: true });
    expect(text).toContain("chain=Gnosis Chain");
    expect(text).toContain("exclude Gnosis Ltd.=yes");
    expect(text).toContain("hidden (spam) tokens=hidden");
  });
});

describe("treasury tabs", () => {
  it("are Overview | Assets | Wallets | History, overview first", () => {
    expect(TREASURY_TABS.map((tab) => tab.id)).toEqual(["overview", "assets", "wallets", "history"]);
    expect(DEFAULT_TREASURY_TAB).toBe("overview");
  });

  it("name only groups that exist in SECTION_GROUPS.treasury, and reach them all", () => {
    const groups = new Set(Object.keys(SECTION_GROUPS.treasury));
    const named = new Set(TREASURY_TABS.flatMap((tab) => [...tab.groups]));
    for (const group of named) expect(groups.has(group), group).toBe(true);
    expect(named).toEqual(groups);
    expect(groupsForTab("history")).toEqual(["history"]);
    expect(groupsForTab("overview")).toEqual(["core", "history"]);
  });

  it("coerce unknown values to the default and resolve aliases", () => {
    expect(toTreasuryTab("wallets")).toBe("wallets");
    expect(toTreasuryTab("portfolio")).toBe("overview");
    expect(toTreasuryTab("nope")).toBe("overview");
    expect(resolveTreasuryTab("nope")).toBeNull();
    expect(resolveTreasuryTab("constructor")).toBeNull();
  });
});
