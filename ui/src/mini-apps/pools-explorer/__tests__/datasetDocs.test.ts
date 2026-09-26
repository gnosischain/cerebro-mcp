import { describe, expect, it } from "vitest";

import { COLUMN_CONFIGS, defaultHidden, kindForColumn, resolveColumnPolicy } from "../model/columns";
import { DATASET_DOCS } from "../model/datasetDocs";
import { ALL_DATASET_KEYS, SECTION_GROUPS } from "../model/datasetGroups";
import { DATASET_COLUMNS, POOL_DIRECTORY_COLUMNS } from "../types";

describe("dataset docs completeness", () => {
  it("documents every dataset key with a what and a method", () => {
    const missing = ALL_DATASET_KEYS.filter((key) => !DATASET_DOCS[key]?.what || !DATASET_DOCS[key]?.method);
    expect(missing).toEqual([]);
  });

  it("documents nothing that is not a dataset", () => {
    const unknown = Object.keys(DATASET_DOCS).filter((key) => !ALL_DATASET_KEYS.includes(key));
    expect(unknown).toEqual([]);
  });

  it("carries the four load-bearing disclosures", () => {
    const all = Object.values(DATASET_DOCS).map((doc) => `${doc.what} ${doc.method ?? ""}`).join("\n");
    expect(all).toContain("cl_below_active_threshold");
    expect(all).toContain("raw");
    expect(all).toContain("full-range position");
    expect(all).toContain("2022-12-12");
    expect(DATASET_DOCS.pool_profile_heatmap.method).toContain("on demand");
    expect(DATASET_DOCS.token_pools.method).toContain("never summed across pairs");
  });

  it("says the as-of is a complete SERVED day and the provenance is the served attempt", () => {
    // Lesson published-is-not-served: a raw publication is not what the views
    // serve, and the newest raw day is often one the indexer is still writing.
    expect(DATASET_DOCS.pools_summary.method).toContain("v_publications_current");
    expect(DATASET_DOCS.pools_summary.method).toContain("COMPLETE served day");
    expect(DATASET_DOCS.pool_publication_facts.method).toContain("v_publications_current");
    expect(DATASET_DOCS.pool_publication_facts.method).not.toContain("Both jobs' rows");
  });
});

describe("column policy", () => {
  it("maps every COLUMN_CONFIGS entry to a known dataset key and known columns", () => {
    for (const [key, specs] of Object.entries(COLUMN_CONFIGS)) {
      expect(Object.keys(SECTION_GROUPS).length).toBeGreaterThan(0);
      const columns = DATASET_COLUMNS[key as keyof typeof DATASET_COLUMNS] as readonly string[] | undefined;
      expect(columns, `${key} is not a dataset`).toBeDefined();
      for (const spec of specs) {
        expect(columns, `${key}.${spec.key} is not a column`).toContain(spec.key);
      }
    }
  });

  it("hides helper columns the composed cells consume, and raw twins of float/units columns", () => {
    const policy = resolveColumnPolicy("pool_directory", [...POOL_DIRECTORY_COLUMNS]);
    expect(policy.hidden).toEqual(expect.arrayContaining([
      "token0_symbol", "token0_decimals", "token0_resolved", "token0_label",
      "token1_symbol", "token1_decimals", "token1_resolved", "token1_label",
      "assets", "asset_symbols", "asset_decimals", "liquidity_raw", "reserve0_raw", "reserve1_raw",
      // Hidden as a column but NOT swallowed: the is_live cell renders it.
      "has_state",
    ]));
    expect(policy.hidden).not.toContain("pool_address");
    expect(policy.hidden).not.toContain("liquidity_float");
    expect(policy.kinds.pool_address).toBe("pool");
    expect(policy.entities.pool_address).toBe("pool");
    expect(policy.kinds.token0).toBe("token");
    expect(policy.entities.token0).toBe("token");
    expect(policy.kinds.pool_class).toBe("class");
    expect(policy.kinds.fee).toBe("fee");
    expect(policy.kinds.ticks_probed).toBe("probe");
    expect(policy.kinds.liquidity_float).toBe("liquidity");
    expect(policy.kinds.price_raw).toBe("price");
    expect(policy.kinds.reserve0_units).toBe("amount");
    expect(policy.kinds.first_published).toBe("date");
    expect(policy.labels.liquidity_float).toBe("Liquidity (L)");
  });

  it("hides the detail-only reserve arrays and the token_pools decimals helper", () => {
    const detail = resolveColumnPolicy("pool_detail", [...DATASET_COLUMNS.pool_detail]);
    expect(detail.hidden).toEqual(expect.arrayContaining(["reserve_tokens", "reserve_raw", "pool_id", "entity_label"]));
    expect(detail.hidden).not.toContain("profile_available_from");
    const pools = resolveColumnPolicy("token_pools", [...DATASET_COLUMNS.token_pools]);
    expect(pools.hidden).toEqual(expect.arrayContaining(["token_decimals", "counter_labels", "has_state"]));
    expect(pools.kinds.price_of_token_in_counter).toBe("price");
    expect(pools.labels.price_of_token_in_counter).toBe("Price in counter");
    // token_name is the real column and must render, labelled "Name".
    const tokens = resolveColumnPolicy("token_directory", [...DATASET_COLUMNS.token_directory]);
    expect(tokens.hidden).not.toContain("token_name");
    expect(tokens.labels.token_name).toBe("Name");
    // Publication facts name the executor and the observation count.
    const facts = resolveColumnPolicy("pool_publication_facts", [...DATASET_COLUMNS.pool_publication_facts]);
    expect(facts.labels.observations_total).toBe("Observations");
    expect(facts.labels.executor_kind).toBe("Executor");
  });

  it("keeps a bare raw column visible when it has no display twin", () => {
    const columns = [...DATASET_COLUMNS.pool_ticks_at];
    expect(defaultHidden("fee_growth_outside_0_raw", columns)).toBe(false);
    expect(defaultHidden("liquidity_gross_raw", columns)).toBe(true);
    expect(kindForColumn("fee_growth_outside_0_raw")).toBe("raw");
    expect(kindForColumn("checks_passed")).toBe("list");
    expect(kindForColumn("counter_tokens")).toBe("tokenList");
    expect(kindForColumn("is_full_range")).toBe("bool");
    expect(kindForColumn("tick_lower")).toBe("tick");
    expect(kindForColumn("reserve_share")).toBe("share");
  });

  it("renders attempt ids as the UUIDs they are, not as integers", () => {
    // attempt_id sat in the integer pattern, so every attempt in the provenance
    // table rendered as "—" — the one column that names the SERVED attempt.
    expect(kindForColumn("attempt_id")).toBe("hash");
    expect(kindForColumn("publication_id")).toBe("hash");
  });

  it("every dataset resolves a policy with no unlabelled visible column", () => {
    for (const [key, columns] of Object.entries(DATASET_COLUMNS)) {
      const policy = resolveColumnPolicy(key, [...columns]);
      for (const name of columns) {
        if (policy.hidden.includes(name)) continue;
        expect(policy.labels[name], `${key}.${name}`).toBeTruthy();
        expect(policy.kinds[name], `${key}.${name}`).toBeTruthy();
      }
    }
  });
});
