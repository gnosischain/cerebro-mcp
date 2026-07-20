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

  it("places the ranked Relationships table before its canvas below 1440px", () => {
    const tableSelector = ".ge-body--relationships > .ge-ranked-table";
    const canvasSelector = ".ge-body--relationships > .ge-canvas";
    for (const maxWidth of [1439, 899]) {
      const section = compactSectionFor(tableSelector, maxWidth);
      expect(ruleBody(section, tableSelector)).toMatch(/grid-row:\s*1/);
      expect(ruleBody(section, canvasSelector)).toMatch(/grid-row:\s*2/);
      expect(section).toMatch(/\.ge-body--relationships[\s\S]*?overflow-y:\s*auto/);
    }
  });

  it("stacks relationship preview evidence before a full-width graph below 1440px", () => {
    const previewSection = compactSectionFor(
      ".ge-atlas-body.has-preview > .ge-canvas",
      1439,
    );
    expect(ruleBody(previewSection, ".ge-atlas-body.has-preview")).toMatch(
      /grid-template-columns:\s*minmax\(0,\s*1fr\)/,
    );
    expect(
      ruleBody(previewSection, ".ge-atlas-body.has-preview > .ge-atlas-drawer"),
    ).toMatch(/grid-row:\s*1/);
    expect(ruleBody(previewSection, ".ge-atlas-body.has-preview > .ge-canvas")).toMatch(
      /grid-row:\s*2/,
    );
    expect(ruleBody(css, ".ge-body--relationships.details-open .ge-details")).toMatch(
      /position:\s*absolute/,
    );
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
