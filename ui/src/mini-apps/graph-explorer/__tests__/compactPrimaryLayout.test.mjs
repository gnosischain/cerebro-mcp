// @vitest-environment node

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../graph-explorer.css", import.meta.url), "utf8");

function compactSectionFor(selector, maxWidth = 899) {
  const headerFragment = `max-width: ${maxWidth}px`;
  let mediaStart = css.indexOf("@media");
  while (mediaStart >= 0) {
    const headerEnd = css.indexOf("{", mediaStart);
    const nextMedia = css.indexOf("@media", headerEnd + 1);
    const section = css.slice(mediaStart, nextMedia === -1 ? css.length : nextMedia);
    if (css.slice(mediaStart, headerEnd).includes(headerFragment) && section.includes(selector)) {
      return section;
    }
    mediaStart = nextMedia;
  }
  throw new Error(`missing ${selector} inside a ${headerFragment} media query`);
}

function ruleBody(section, selector) {
  const selectorIndex = section.indexOf(selector);
  expect(selectorIndex, `missing compact rule ${selector}`).toBeGreaterThanOrEqual(0);
  const open = section.indexOf("{", selectorIndex);
  const close = section.indexOf("}", open + 1);
  return section.slice(open + 1, close);
}

describe("compact task primary-reading layout", () => {
  it("places the Money movements table before its Sankey below the split-view breakpoint", () => {
    const tableSelector = ".ge-body--money > .ge-money-table";
    const mapSelector = ".ge-body--money > .ge-money-map";
    for (const maxWidth of [1439, 899]) {
      const section = compactSectionFor(tableSelector, maxWidth);
      expect(ruleBody(section, tableSelector)).toMatch(/grid-row:\s*1/);
      expect(ruleBody(section, mapSelector)).toMatch(/grid-row:\s*2/);
      expect(section).toMatch(/\.ge-body--money[\s\S]*?overflow-y:\s*auto/);
    }
  });

  // Relationships gained a persistent picker rail, so the compact stack is
  // rail -> ranked table -> canvas. The invariant under test is unchanged: the
  // keyboard-readable table still precedes the spatial view.
  it("places the ranked Relationships table before its canvas below 1440px", () => {
    const railSelector = ".ge-body--relationships > .ge-rel-rail";
    const tableSelector = ".ge-body--relationships > .ge-ranked-table";
    const canvasSelector = ".ge-body--relationships > .ge-canvas";
    for (const maxWidth of [1439, 899]) {
      const section = compactSectionFor(tableSelector, maxWidth);
      expect(ruleBody(section, railSelector)).toMatch(/grid-row:\s*1/);
      expect(ruleBody(section, tableSelector)).toMatch(/grid-row:\s*2/);
      expect(ruleBody(section, canvasSelector)).toMatch(/grid-row:\s*3/);
      expect(section).toMatch(/\.ge-body--relationships[\s\S]*?overflow-y:\s*auto/);
      // The rail must never be free to grow into the graph's space.
      expect(ruleBody(section, railSelector)).toMatch(/max-height:/);
    }
  });

  // Replaces the old "stacks relationship preview evidence before a full-width
  // graph" case. That behaviour WAS the reported bug: opening a preview widened
  // the rail from 280px to minmax(520px, 40%) — and to a full-width 48vh row
  // below 1440px — so the user got a definition list and a forty-row table
  // where the graph should have been. The rail is now a fixed column, and no
  // rule may widen it for a preview.
  it("never widens the picker rail when a relationship preview is open", () => {
    // Comments may still explain the old behaviour; no *rule* may implement it.
    const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(withoutComments).not.toContain("has-preview");
    expect(ruleBody(css, ".ge-body--relationships,")).toMatch(
      /grid-template-columns:\s*280px/,
    );
    expect(ruleBody(css, ".ge-body--relationships.details-open .ge-details")).toMatch(
      /position:\s*absolute/,
    );
  });

  // A preview hides the ranked table AND the details panel, so the grid must
  // drop to two tracks. Leaving the third reserved left a dead 340px band
  // beside the graph — the space the graph was supposed to reclaim.
  it("drops the third column when the analysis panes are hidden", () => {
    expect(ruleBody(css, ".ge-body--relationships.no-analysis,")).toMatch(
      /grid-template-columns:\s*280px\s+minmax\(0,\s*1fr\)\s*;/,
    );
  });

  // The preview card's children are all intrinsic. A leftover row template
  // sized for the deleted 40-row sample table stretched the "Add to graph"
  // button into a ~260px hole.
  it("sizes the preview card to its content", () => {
    const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(withoutComments).not.toMatch(
      /\.ge-relation-preview\s*\{[^}]*minmax\(180px,\s*1fr\)/,
    );
    expect(withoutComments).not.toMatch(
      /\.ge-relation-preview\s*\{[^}]*flex:\s*1 1 auto/,
    );
  });

  // The centred empty-state hint spans the whole stage, so it must not eat
  // clicks aimed at the recovery action rendered inside it.
  it("lets the empty-state body receive pointer events", () => {
    expect(ruleBody(css, ".ge-placeholder {")).toMatch(/pointer-events:\s*none/);
    expect(ruleBody(css, ".ge-placeholder__body")).toMatch(/pointer-events:\s*auto/);
  });

  it("keeps Transaction Detail full width while its inspector overlays", () => {
    expect(ruleBody(css, ".ge-body--tx-detail,")).toMatch(
      /grid-template-columns:\s*minmax\(0,\s*1fr\)/,
    );
    const inspector = ruleBody(css, ".ge-body--tx-detail.details-open .ge-details");
    expect(inspector).toMatch(/position:\s*absolute/);
    expect(inspector).toMatch(/inset:\s*0 0 0 auto/);
  });
});
