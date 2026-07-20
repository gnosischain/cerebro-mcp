// @vitest-environment jsdom
// URL round-trip + unmanaged-param preservation for the graph deep links.

import { beforeEach, describe, expect, it } from "vitest";
import { TASK_OF_MODE } from "../TaskSwitch";
import { readUrl, writeUrl, type GraphUrlState } from "../urlState";

function setSearch(qs: string) {
  window.history.replaceState({}, "", `/graph${qs}`);
}

const BASE: GraphUrlState = {
  mode: "atlas",
  seed: "",
  profiles: [],
  window: 0,
  max: 0,
  sel: "",
  esel: "",
  status: "all",
  layout: "force",
  depth: 1,
  tgrain: "week",
  trange: 365,
  twin: 4,
  fseeds: [],
  fdir: "out",
  fhops: 2,
  fmin: 10,
  frange: 30,
  ftok: [],
  txhashes: [],
  txseed: "",
  txcounterparties: [],
  txtokens: [],
  txrange: 30,
  txmax: 25,
  txt0: "",
  txt1: "",
};

describe("graph urlState", () => {
  beforeEach(() => setSearch(""));

  it("round-trips a full state", () => {
    writeUrl({
      ...BASE,
      mode: "investigate",
      seed: "0xabc",
      profiles: ["circles_trust", "safe_ownership"],
      window: 30,
      max: 50,
      sel: "0xdef",
      status: "approved",
      layout: "circular",
      depth: 3,
    });
    const u = readUrl();
    expect(u.mode).toBe("investigate");
    expect(u.seed).toBe("0xabc");
    expect(u.profiles).toEqual(["circles_trust", "safe_ownership"]);
    expect(u.window).toBe(30);
    expect(u.max).toBe(50);
    expect(u.sel).toBe("0xdef");
    expect(u.status).toBe("approved");
    expect(u.layout).toBe("circular");
    expect(u.depth).toBe(3);
  });

  it("round-trips flows deep-link state", () => {
    writeUrl({
      ...BASE,
      mode: "flows",
      fseeds: ["0xv1c", "0xexp"],
      fdir: "both",
      fhops: 3,
      fmin: 100,
      frange: 90,
      ftok: ["0xtok1"],
    });
    const u = readUrl();
    expect(u.mode).toBe("flows");
    expect(u.fseeds).toEqual(["0xv1c", "0xexp"]);
    expect(u.fdir).toBe("both");
    expect(u.fhops).toBe(3);
    expect(u.fmin).toBe(100);
    expect(u.frange).toBe(90);
    expect(u.ftok).toEqual(["0xtok1"]);
  });

  it("round-trips Transaction Detail state while retaining the legacy mode route", () => {
    writeUrl({
      ...BASE,
      mode: "transactions",
      txhashes: ["0xhash1", "0xhash2"],
      txcounterparties: ["0xparty"],
      txtokens: ["0xtoken"],
      txrange: 90,
      txmax: 50,
      txt0: "2026-01-01T00:00:00Z",
      txt1: "2026-02-01T00:00:00Z",
    });
    const u = readUrl();
    expect(u.mode).toBe("transactions");
    expect(u.txhashes).toEqual(["0xhash1", "0xhash2"]);
    expect(u.txcounterparties).toEqual(["0xparty"]);
    expect(u.txtokens).toEqual(["0xtoken"]);
    expect(u.txrange).toBe(90);
    expect(u.txmax).toBe(50);
    expect(u.txt0).toBe("2026-01-01T00:00:00Z");
    expect(u.txt1).toBe("2026-02-01T00:00:00Z");
  });

  it("omits defaults entirely (clean URLs)", () => {
    writeUrl(BASE);
    expect(window.location.search).toBe("");
  });

  it("preserves unmanaged params like ?token=", () => {
    setSearch("?token=dev&custom=1");
    writeUrl({ ...BASE, seed: "0xabc" });
    const p = new URLSearchParams(window.location.search);
    expect(p.get("token")).toBe("dev");
    expect(p.get("custom")).toBe("1");
    expect(p.get("seed")).toBe("0xabc");
    // rewriting drops managed keys that returned to defaults
    writeUrl(BASE);
    const p2 = new URLSearchParams(window.location.search);
    expect(p2.get("token")).toBe("dev");
    expect(p2.get("seed")).toBeNull();
  });

  it("maps all legacy modes onto the three public tasks", () => {
    expect(TASK_OF_MODE).toEqual({
      atlas: "relationships",
      investigate: "relationships",
      flows: "money",
      timeline: "money",
      transactions: "tx",
    });
  });
});
