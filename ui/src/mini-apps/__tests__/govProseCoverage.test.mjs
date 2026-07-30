// Every tag the sanitizer lets through must have a `.gov-post-body` rule.
//
// The bug this exists for: `.gov-post-body` styled p / blockquote / pre / code /
// table / th / td / a and NOTHING else. Proposal bodies and forum posts render
// through it, so every heading, list and rule in a Snapshot proposal or a
// Discourse post came out as undifferentiated body text. Two global rules make
// the omission total rather than merely unstyled:
//
//   themes/global.css  ->  * { margin: 0; padding: 0 }
//   Tailwind preflight ->  h1..h6 { font-size: inherit; font-weight: inherit }
//                          ol, ul { list-style: none }
//
// so an unstyled <h2> is EXACTLY body text with no space above it, and an
// unstyled <ul> has no marker and no indent. "The text is not formatted."
//
// The two files drift in one direction — someone widens ALLOWED_TAGS to render
// more of a post, and the new tag silently arrives unstyled. This pins them
// together.

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const CSS = readFileSync(
  new URL("../governance/governance.css", import.meta.url).pathname,
  "utf8",
);
const SANITIZE = readFileSync(
  new URL("../governance/model/sanitize.ts", import.meta.url).pathname,
  "utf8",
);

/** ALLOWED_TAGS from model/sanitize.ts, read rather than duplicated.
 *
 * Anchored on `ALLOWED_TAGS:` (with the colon) and closed at the array's own
 * `]`, NOT bounded by the next key name. The first draft sliced from
 * `indexOf("ALLOWED_TAGS")` to `indexOf("ALLOWED_ATTR")` and got an EMPTY
 * string, because sanitize.ts's header comment mentions ALLOWED_ATTR eight
 * lines above the actual ALLOWED_TAGS key — so the end index preceded the
 * start. The coverage assertion below then passed vacuously against an empty
 * allowlist. Same mistake class as `sql-guard-counts-comments-as-code`: a
 * textual guard that reads prose as code. Hence the non-triviality test. */
function allowedTags() {
  const start = SANITIZE.indexOf("ALLOWED_TAGS:");
  const end = SANITIZE.indexOf("]", start);
  if (start < 0 || end < 0) throw new Error("ALLOWED_TAGS array not found");
  return [...SANITIZE.slice(start, end).matchAll(/"([a-z0-9]+)"/g)].map((m) => m[1]);
}

/** Every element name targeted by a `.gov-post-body` selector.
 *
 * Tokenizes the whole selector rather than grabbing the identifier next to the
 * brace. The first draft did the latter and reported `aside` and `thead` as
 * unstyled when both ARE styled — it read `.gov-post-body aside.quote` as
 * "quote" (the class) and `.gov-post-body thead th` as "th" (the last tag). A
 * guard that is wrong about what it is looking at is worse than no guard. */
function styledTags() {
  const out = new Set();
  for (const rule of CSS.split("}")) {
    const selector = rule.slice(0, rule.indexOf("{"));
    if (!selector.includes(".gov-post-body")) continue;
    for (const part of selector.split(",")) {
      if (!part.includes(".gov-post-body")) continue;
      const bare = part
        .replace(/\.gov-post-body/g, " ")
        // Drop classes, ids, pseudo-classes and pseudo-elements, so only bare
        // element names survive: `aside.quote` -> `aside`, `li::marker` -> `li`.
        .replace(/[.#][a-zA-Z_-][\w-]*/g, " ")
        .replace(/::?[a-zA-Z-]+(\([^)]*\))?/g, " ")
        .replace(/[>+~]/g, " ");
      for (const token of bare.split(/\s+/)) {
        if (/^[a-z][a-z0-9]*$/.test(token)) out.add(token);
      }
    }
  }
  return out;
}

// Tags that carry no visual weight of their own, so a missing rule is not a bug:
// `br` is a line break, and `span` / `div` are neutral containers the sanitizer
// keeps only so Discourse's wrappers survive.
const NO_STYLING_NEEDED = new Set(["br", "span", "div"]);
// Structural table parts inherit from the `table` / `th` / `td` rules.
const INHERITS = new Set(["tbody", "tr"]);

describe("governance prose block", () => {
  it("styles every tag the sanitizer allows", () => {
    const styled = styledTags();
    const missing = allowedTags().filter(
      (t) => !styled.has(t) && !NO_STYLING_NEEDED.has(t) && !INHERITS.has(t),
    );
    expect(missing).toEqual([]);
  });

  it("reads a non-trivial allowlist and a non-trivial rule set", () => {
    // Both halves are parsed out of files with regexes, and a regex that stops
    // matching would make the test above pass vacuously — which is the failure
    // mode this repo keeps hitting.
    expect(allowedTags().length).toBeGreaterThan(20);
    expect(styledTags().size).toBeGreaterThan(15);
    expect(allowedTags()).toContain("h4");
    expect(allowedTags()).toContain("aside");
  });

  it("restores list markers and indent that the global resets removed", () => {
    expect(CSS).toMatch(/\.gov-post-body ul\{[^}]*list-style:\s*disc/);
    expect(CSS).toMatch(/\.gov-post-body ol\{[^}]*list-style:\s*decimal/);
    expect(CSS).toMatch(/\.gov-post-body ul,\.gov-post-body ol\{[^}]*padding-left/);
  });

  it("gives headings weight and space, which the preflight strips", () => {
    const heading = CSS.match(
      /\.gov-post-body h1,[\s\S]*?\.gov-post-body h6\{([^}]*)\}/,
    );
    expect(heading, "no combined h1..h6 rule").not.toBeNull();
    expect(heading[1]).toMatch(/font-weight/);
    expect(heading[1]).toMatch(/margin/);
    // Distinct sizes, or the hierarchy is still invisible.
    const sizes = ["h1", "h2", "h3", "h4"].map((h) => {
      const m = CSS.match(new RegExp(`\\.gov-post-body ${h}\\{[^}]*font-size:\\s*([\\d.]+)px`));
      return m ? Number(m[1]) : null;
    });
    expect(sizes.every((s) => s !== null)).toBe(true);
    expect(new Set(sizes).size).toBe(sizes.length);
  });

  it("does NOT reuse .report-html, whose scale and section badge are wrong here", () => {
    // .report-html h2 carries a counter-increment + ::before badge; applying it
    // to a forum post would number every heading as a report section.
    expect(CSS).not.toMatch(/\.gov-post-body[^{]*\.report-html/);
    expect(CSS).not.toMatch(/\.gov-post-body h2\{[^}]*counter-increment/);
  });
});
