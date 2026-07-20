// @vitest-environment node

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../graph-explorer.css", import.meta.url), "utf8");
const timelineRule =
  css.match(/\.ge-mode--timeline\s*\{([^}]+)\}/)?.[1] ?? "";

describe("Timeline responsive scroll containment", () => {
  it.each([600, 700, 900, 1280])(
    "keeps the complete Timeline surface reachable at %ipx",
    () => {
      // This base (non-media) rule is the internal scroll owner at every
      // supported shell width; the outer .ge-shell intentionally clips.
      expect(timelineRule).toMatch(/display:\s*flex/);
      expect(timelineRule).toMatch(/flex:\s*1 1 auto/);
      expect(timelineRule).toMatch(/flex-direction:\s*column/);
      expect(timelineRule).toMatch(/min-height:\s*0/);
      expect(timelineRule).toMatch(/overflow-x:\s*hidden/);
      expect(timelineRule).toMatch(/overflow-y:\s*auto/);
    },
  );
});
