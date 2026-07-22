import { describe, expect, it } from "vitest";
import { DATASET_DOCS } from "../model/datasetDocs";
import { DATASET_GROUP, SECTION_GROUPS, datasetError } from "../model/datasetGroups";
import { ENTITY_HEADER, ENTITY_LAYOUT } from "../detail/EntityDetail";
import { buildShareHeatmap } from "../model/parseRows";

describe("dataset docs completeness", () => {
  it("documents every section dataset key", () => {
    const missing: string[] = [];
    for (const groups of Object.values(SECTION_GROUPS)) {
      for (const keys of Object.values(groups)) {
        for (const key of keys) {
          if (!DATASET_DOCS[key]?.what) missing.push(key);
        }
      }
    }
    expect(missing).toEqual([]);
  });

  it("documents every entity dataset key (layout + header)", () => {
    const missing: string[] = [];
    for (const layout of Object.values(ENTITY_LAYOUT)) {
      for (const { key } of layout) {
        if (!DATASET_DOCS[key]?.what) missing.push(key);
      }
    }
    for (const key of Object.values(ENTITY_HEADER)) {
      if (!DATASET_DOCS[key]?.what) missing.push(key);
    }
    expect(missing).toEqual([]);
  });

  it("maps every dataset key to exactly one group", () => {
    for (const [key, owner] of Object.entries(DATASET_GROUP)) {
      expect(SECTION_GROUPS[owner.section][owner.group]).toContain(key);
    }
  });
});

describe("datasetError (stub-descriptor failure contract)", () => {
  it("returns the server error message when present", () => {
    expect(
      datasetError({ provenance: { coverage: { error: "boom", warning_codes: ["query_failed"] } } }),
    ).toBe("boom");
  });
  it("falls back to a generic message on query_failed without error text", () => {
    expect(datasetError({ provenance: { coverage: { warning_codes: ["query_failed"] } } })).toBe("Query failed.");
  });
  it("returns empty for healthy datasets", () => {
    expect(datasetError({ provenance: { coverage: { warning_codes: [] } } })).toBe("");
    expect(datasetError(undefined)).toBe("");
  });
});

describe("buildShareHeatmap", () => {
  const rows = [
    { pair: "A/B", solver: "s1", fills: 10, share: 0.5 },
    { pair: "A/B", solver: "s2", fills: 10, share: 0.5 },
    { pair: "C/D", solver: "s1", fills: 100, share: 1.0 },
    { pair: "", solver: "s1", fills: 5, share: 0.2 },
    { pair: "E/F", solver: "s1", fills: 1, share: Number.NaN },
  ];
  const model = buildShareHeatmap({
    rows,
    rowLabel: (row) => String(row.pair),
    colLabel: (row) => String(row.solver),
    weightField: "fills",
    shareField: "share",
    maxRows: 2,
    maxCols: 2,
  });
  it("keeps the heaviest rows/cols and drops invalid entries", () => {
    expect(model.yLabels).toEqual(["C/D", "A/B"]);
    expect(model.xLabels[0]).toBe("s1");
    expect(model.cells.length).toBeGreaterThan(0);
    for (const [, , share] of model.cells) {
      expect(share).toBeGreaterThanOrEqual(0);
      expect(share).toBeLessThanOrEqual(1);
    }
  });
  it("indexes cells against the kept label arrays", () => {
    for (const [x, y] of model.cells) {
      expect(x).toBeLessThan(model.xLabels.length);
      expect(y).toBeLessThan(model.yLabels.length);
    }
  });
});
