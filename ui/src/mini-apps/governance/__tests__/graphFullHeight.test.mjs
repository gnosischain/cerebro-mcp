// The graph tab fills the fold with CSS alone. This is asserted rather than
// eyeballed because it was got wrong three times in a row:
//
//   `calc(100vh - 70px)`      — the app renders inside `.ma-body`, a scroll
//                               container below the chrome, so any N is wrong.
//   `.ma-body.clientHeight`   — reported 46px, because a stylesheet I had just
//                               imported broke the ancestor flex chain.
//   a JS `useFitHeight` hook   — measured correctly and still wrong: it existed
//                               only to compensate for that broken cascade.
//
// The chain that actually works is the one graph-explorer already proved, with
// no JS: every level is either `flex: 0 0 auto` (chrome) or
// `flex: 1 1 auto; min-height: 0` (filler), and the grid's default `stretch`
// matches the aside to the chart for free.

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../governance.css", import.meta.url), "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "");

/** The declaration block for an exact top-level selector, or "" if absent. */
function rule(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`, "m").exec(css);
  return match ? match[1] : "";
}

describe("governance graph tab sizes itself from the flex chain", () => {
  it("does not encode a window height inside the app body", () => {
    // Any `100vh` on an element that renders inside `.ma-body` is the same
    // mistake, not just the `.gov-body` one. `.gov-loading` is exempt: it
    // replaces the whole chrome while the view is still null, so there it is
    // measuring the actual viewport.
    const offenders = [];
    const re = /(?:^|\})([^{}]+)\{([^}]*)\}/g;
    for (let m = re.exec(css); m; m = re.exec(css)) {
      const selector = m[1].trim();
      if (selector.startsWith("@") || selector.includes(".gov-loading")) continue;
      if (m[2].includes("100vh")) offenders.push(selector);
    }
    expect(offenders).toEqual([]);
  });

  it("makes the flush body a flex column with no padding", () => {
    // `.ma-body` is a plain block, so without display:flex the content cannot
    // claim the remaining height no matter what it declares — and `.ma-body`'s
    // own `padding: 14px 18px 32px` stays unless this class zeroes it, which
    // left the graph 46px short of the fold the first time round.
    const body = rule(".gov-body--flush");
    expect(body).toMatch(/display:\s*flex/);
    expect(body).toMatch(/flex-direction:\s*column/);
    expect(body).toMatch(/padding:\s*0/);
    expect(body).toMatch(/overflow:\s*hidden/);
  });

  it("lets the flush content column fill and shrink", () => {
    const content = rule(".gov-content--flush");
    expect(content).toMatch(/flex:\s*1 1 auto/);
    expect(content).toMatch(/min-height:\s*0/);
    expect(content).toMatch(/padding:\s*0/);
  });

  it("cancels the centring auto margin it inherits", () => {
    // `.gov-content` sets `margin: 0 auto` to centre a max-width page. Inside a
    // flex column an auto CROSS-AXIS margin absorbs the free space instead of
    // stretching, so the graph collapsed to fit-content and sat centred with
    // ~400px dead on each side of a wide window.
    const content = rule(".gov-content--flush");
    expect(content).toMatch(/margin:\s*0(?![\s]*auto)/);
  });

  it("gives the toolbar its own height and the graph row the rest", () => {
    expect(rule(".gov-graph-bar")).toMatch(/flex:\s*0 0 auto/);
    const layout = rule(".gov-graph-layout");
    expect(layout).toMatch(/flex:\s*1 1 auto/);
    expect(layout).toMatch(/min-height:\s*0/);
    // `align-items: start` defeats the grid row-stretch that matches the aside
    // to the chart — it is precisely why the side panel was getting cut.
    expect(layout).not.toMatch(/align-items/);
  });

  it("matches the aside to the chart via stretch, not a height", () => {
    const side = rule(".gov-graph-side");
    expect(side).toMatch(/min-height:\s*0/);
    expect(side).toMatch(/overflow-y:\s*auto/);
    // Any height/max-height here re-introduces the JS-measured layout.
    expect(side).not.toMatch(/(?:^|;)\s*(?:max-)?height:/);
  });

  it("does not nest a third scrollbar inside the aside", () => {
    expect(rule(".gov-cites__list")).not.toMatch(/max-height|overflow/);
  });

  it("declares .gov-graph-summary exactly once", () => {
    // There were two copies; the stale one left a margin on a centered flex
    // item and the live one silently overrode it.
    const hits = css.match(/(?:^|\})\s*\.gov-graph-summary\s*\{/gm) ?? [];
    expect(hits).toHaveLength(1);
  });
});
