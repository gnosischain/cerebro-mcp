// A `var(--token)` that was never defined makes the WHOLE declaration invalid at
// computed-value time, so the browser drops it — silently. No console warning, no
// error, no visual hint that a rule exists at all.
//
// This is how the governance Overview's two top cards ended up with no border and
// no background: `.gov-livenow__col` set them via `--border-subtle` and
// `--surface-raised`, neither of which is defined anywhere in the repo. Measured
// in the browser: `borderTopWidth: 0px`, `backgroundColor: rgba(0,0,0,0)`, on
// elements that carry the class. `--accent` had the same problem across 7
// declarations.
//
// Grepping for the definition is the only way to catch it, so: grep.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = new URL("../../", import.meta.url).pathname; // ui/src/

function cssFiles(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) cssFiles(full, out);
    else if (entry.endsWith(".css")) out.push(full);
  }
  return out;
}

const FILES = cssFiles(ROOT);
const ALL_CSS = FILES.map((f) => readFileSync(f, "utf8")).join("\n");

/** Every custom property ASSIGNED anywhere (`--x: value`), on any selector. */
const defined = new Set(
  [...ALL_CSS.matchAll(/(^|[;{\s])(--[a-z0-9-]+)\s*:/gi)].map((m) => m[2]),
);

/** Every custom property READ (`var(--x)`), with the file it was read in. */
function readsIn(text) {
  return [...text.matchAll(/var\(\s*(--[a-z0-9-]+)\s*([,)])/g)].map((m) => ({
    token: m[1],
    hasFallback: m[2] === ",",
  }));
}

// Tokens these mini-apps are known to rely on and which are NOT yet defined.
// Pre-existing in apps outside the current change; listed so this test can gate
// the ones already fixed without failing on unrelated debt. Shrink-only: deleting
// a line that no longer has a violation is the point.
const KNOWN_UNDEFINED = new Set([
  "--text", // graph-explorer, 11 uses
  "--danger",
  "--danger-bg", // graph-explorer error states
  "--surface-subtle", // graph-explorer + transaction-detail
  "--radius-md", // data-catalog
  "--mono", // model-lineage
  "--ge-money-table-width", // graph-explorer money table
]);

describe("every CSS custom property that is read is also defined", () => {
  it.each(FILES.map((f) => [f.replace(ROOT, ""), f]))(
    "%s",
    (_label, file) => {
      const missing = [
        ...new Set(
          readsIn(readFileSync(file, "utf8"))
            // `var(--x, fallback)` is safe by construction.
            .filter((r) => !r.hasFallback)
            .map((r) => r.token)
            .filter((t) => !defined.has(t) && !KNOWN_UNDEFINED.has(t)),
        ),
      ];
      expect(missing).toEqual([]);
    },
  );

  it("the known-undefined allowlist is shrink-only", () => {
    // An entry that no longer corresponds to a real violation must be deleted,
    // so the backlog can only go down.
    const read = new Set(readsIn(ALL_CSS).map((r) => r.token));
    const stale = [...KNOWN_UNDEFINED].filter(
      (t) => defined.has(t) || !read.has(t),
    );
    expect(stale).toEqual([]);
  });

  it("the tokens that broke the governance cards are now defined or replaced", () => {
    for (const token of ["--border-subtle", "--surface-raised", "--accent"]) {
      const stillRead = readsIn(ALL_CSS).some(
        (r) => r.token === token && !r.hasFallback,
      );
      expect(
        defined.has(token) || !stillRead,
        `${token} is read without a fallback but never defined`,
      ).toBe(true);
    }
  });
});
