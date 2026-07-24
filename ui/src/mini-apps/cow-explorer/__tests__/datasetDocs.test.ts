import { describe, expect, it } from "vitest";
import { DATASET_DOCS } from "../model/datasetDocs";
import { DATASET_GROUP, SECTION_GROUPS } from "../model/datasetGroups";
import { ENTITY_HEADER, ENTITY_LAYOUT } from "../detail/EntityDetail";
import { COLUMN_CONFIGS, resolveColumnPolicy } from "../model/columns";
import { buildShareHeatmap } from "../model/parseRows";

function knownDatasetKeys(): Set<string> {
  const known = new Set<string>();
  for (const groups of Object.values(SECTION_GROUPS)) {
    for (const keys of Object.values(groups)) {
      for (const key of keys) known.add(key);
    }
  }
  for (const layout of Object.values(ENTITY_LAYOUT)) {
    for (const { key } of layout) known.add(key);
  }
  for (const key of Object.values(ENTITY_HEADER)) known.add(key);
  return known;
}

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

describe("column policy", () => {
  it("maps every COLUMN_CONFIGS entry to a known dataset key", () => {
    const known = knownDatasetKeys();
    const unknown = Object.keys(COLUMN_CONFIGS).filter((key) => !known.has(key));
    expect(unknown).toEqual([]);
  });

  it("hides pair_depth helper columns and labels the ladder columns", () => {
    const policy = resolveColumnPolicy("pair_depth", [
      "order_uid", "owner", "kind", "side", "order_class", "partially_fillable",
      "creation_date", "valid_to", "sell_token", "buy_token", "sell_symbol",
      "buy_symbol", "sell_decimals", "buy_decimals", "price", "amount_base",
      "amount_quote", "sell_amount_raw", "buy_amount_raw", "indexed_from",
      "indexed_to", "source_observed_at",
    ]);
    expect(policy.hidden).toEqual(expect.arrayContaining([
      "sell_symbol", "buy_symbol", "sell_decimals", "buy_decimals",
      "sell_amount_raw", "buy_amount_raw", "indexed_from", "indexed_to",
      "source_observed_at",
    ]));
    expect(policy.labels.price).toBe("Limit price");
    expect(policy.labels.amount_base).toBe("Amount (base)");
    expect(policy.labels.amount_quote).toBe("Amount (quote)");
    expect(policy.kinds.sell_token).toBe("token");
    expect(policy.kinds.owner).toBe("address");
    expect(policy.entities.order_uid).toBe("order");
  });

  it("resolves solver-directory and score-gap solver columns as solver entities", () => {
    const directory = resolveColumnPolicy("solver_directory", ["chain_id", "solver", "chain_anchor_at"]);
    expect(directory.kinds.solver).toBe("solver");
    expect(directory.entities.solver).toBe("solver");
    expect(directory.kinds.chain_anchor_at).toBe("time");
    expect(directory.labels.chain_anchor_at).toBe("Chain latest");
    const gaps = resolveColumnPolicy("solver_score_gaps", ["chain_id", "competition_solver"]);
    expect(gaps.kinds.competition_solver).toBe("solver");
    expect(gaps.entities.competition_solver).toBe("solver");
  });

  it("renders trader-dynamics periods and retention cohorts as time columns", () => {
    const dynamics = resolveColumnPolicy("trader_dynamics", ["period", "quick_ratio"]);
    expect(dynamics.kinds.period).toBe("time");
    expect(dynamics.labels.quick_ratio).toBe("Quick ratio");
    const retention = resolveColumnPolicy("trader_retention", ["cohort_month", "month_index"]);
    expect(retention.kinds.cohort_month).toBe("time");
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
