// @vitest-environment jsdom
// URL round-trip + unmanaged-param preservation for the graph deep links.

import { beforeEach, describe, expect, it } from "vitest";
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
});
