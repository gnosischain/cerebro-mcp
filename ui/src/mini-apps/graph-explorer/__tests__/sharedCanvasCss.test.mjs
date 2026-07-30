// graph-explorer.css is imported by OTHER mini-apps (governance reuses the
// WebGL canvas subsystem), so it must contain no unscoped selectors.
//
// This regressed once and the symptom was remote from the cause: importing the
// stylesheet applied `html, body, #root { height: 100% }` and
// `body { overflow: hidden }` to the whole governance app, which changed the
// height cascade and silently misaligned unrelated chart grids two tabs away.
// The global rules now live in graph-explorer-shell.css, imported only by
// graph-explorer-main.tsx.

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../graph-explorer.css", import.meta.url), "utf8");
/** Comments stripped — the file explains the moved rules in prose, and a raw
 * regex would match that explanation rather than a real declaration. */
const cssCode = css.replace(/\/\*[\s\S]*?\*\//g, "");
const shell = readFileSync(
  new URL("../graph-explorer-shell.css", import.meta.url),
  "utf8",
);

/** The declaration block for an exact top-level selector, or "" if absent. */
function ruleBody(text, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`, "m").exec(text);
  return match ? match[1] : "";
}

/** Every selector list at brace depth 0, comments stripped. */
function topLevelSelectors(text) {
  const nc = text.replace(/\/\*[\s\S]*?\*\//g, "");
  const out = [];
  let depth = 0;
  let buf = "";
  for (const ch of nc) {
    if (ch === "{") {
      if (depth === 0) {
        const sel = buf.trim();
        if (sel && !sel.startsWith("@")) out.push(sel);
        buf = "";
      }
      depth += 1;
    } else if (ch === "}") {
      depth = Math.max(0, depth - 1);
      buf = "";
    } else {
      buf += ch;
    }
  }
  return out;
}

describe("graph-explorer.css is safe to share", () => {
  it("has no selector that escapes the .ge- namespace", () => {
    const escapes = topLevelSelectors(css)
      .flatMap((list) => list.split(",").map((s) => s.trim()))
      .filter(Boolean)
      .filter((sel) => !sel.includes(".ge-") && !sel.includes(":root"));
    expect(escapes).toEqual([]);
  });

  it("does not set document-level height or overflow", () => {
    // These are the exact two rules whose leak caused the regression.
    expect(cssCode).not.toMatch(/^\s*html\s*,/m);
    expect(cssCode).not.toMatch(/^\s*body\s*\{/m);
    expect(cssCode).not.toMatch(/#root/);
  });

  it("does not make the canvas toolbar a scroll container", () => {
    // `overflow-x: auto` on .ge-graph-controls forces overflow-y to compute to
    // `auto` (CSS Overflow L3), turning a 28px button row into a clipping box.
    // Every dropdown anchored below it — `⚙ Forces`, `Advanced` — became
    // invisible when opened, and no z-index could rescue them because clipping
    // is independent of stacking.
    const rule = ruleBody(cssCode, ".ge-graph-controls");
    expect(rule).toBeTruthy();
    expect(rule).not.toMatch(/overflow/);
    expect(rule).toMatch(/flex-wrap:\s*wrap/);
  });

  it("lets the legend scroll instead of truncating rows", () => {
    // .ge-legend-title is `flex: 0 0 100%`, so any graph with more than ~2
    // kinds wraps past the max-height cap. With `overflow-y: hidden` those rows
    // were unreachable.
    const rule = ruleBody(cssCode, ".ge-legend-body");
    expect(rule).toBeTruthy();
    expect(rule).not.toMatch(/overflow-y:\s*hidden/);
    expect(rule).toMatch(/overflow-y:\s*auto/);
  });

  it("keeps those globals in the app-shell stylesheet instead", () => {
    // If someone deletes the shell file, graph-explorer itself breaks — assert
    // the rules still exist SOMEWHERE rather than silently losing them.
    expect(shell).toMatch(/#root/);
    expect(shell).toMatch(/height:\s*100%/);
    expect(shell).toMatch(/overflow:\s*hidden/);
  });
});
