import { describe, expect, it } from "vitest";

import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import { datasetDisplayState, groupBannerState } from "../model/datasetState";

function descriptor(overrides: {
  rows?: number;
  truncated?: boolean;
  warningCodes?: string[];
  error?: string;
  statsTruncated?: boolean;
}): DatasetDescriptor {
  const rows = overrides.rows ?? 3;
  return {
    key: "k", title: "k", sql: "SELECT 1", database: "governance_db",
    columns: [{ name: "a", type: "UInt64" }],
    stats: {
      row_count: rows, rows_returned: rows, mode: "exact_capped",
      source_rows: rows, row_cap: 10000,
      truncated: overrides.statsTruncated ?? false, warnings: [],
    },
    preview_rows: Array.from({ length: rows }, (_, i) => [i]),
    provenance: {
      coverage: {
        warning_codes: overrides.warningCodes ?? [],
        ...(overrides.error ? { error: overrides.error } : {}),
        ...(overrides.truncated !== undefined ? { truncated: overrides.truncated } : {}),
      },
    },
  };
}

describe("datasetDisplayState", () => {
  it("missing descriptor: loading while the group streams, failed once the group claims loaded", () => {
    expect(datasetDisplayState(undefined, false)).toBe("loading");
    expect(datasetDisplayState(undefined, undefined)).toBe("loading");
    expect(datasetDisplayState(undefined, true)).toBe("failed");
    expect(datasetDisplayState(undefined, "partial")).toBe("failed");
  });

  it("query_failed stub descriptor -> failed (even inside a partial group)", () => {
    expect(datasetDisplayState(descriptor({ rows: 0, warningCodes: ["query_failed"] }), "partial")).toBe("failed");
    expect(datasetDisplayState(descriptor({ rows: 0, error: "timeout" }), true)).toBe("failed");
  });

  it("partial group alone never fails a healthy dataset", () => {
    expect(datasetDisplayState(descriptor({}), "partial")).toBe("ready");
  });

  it("hydration phases override display for healthy descriptors", () => {
    expect(datasetDisplayState(descriptor({}), true, "loading")).toBe("loading");
    expect(datasetDisplayState(descriptor({}), true, "failed")).toBe("failed");
    expect(datasetDisplayState(descriptor({}), true, "complete")).toBe("ready");
  });

  it("result_truncated code, coverage.truncated, or stats.truncated -> truncated", () => {
    expect(datasetDisplayState(descriptor({ warningCodes: ["result_truncated"] }), true)).toBe("truncated");
    expect(datasetDisplayState(descriptor({ truncated: true }), true)).toBe("truncated");
    expect(datasetDisplayState(descriptor({ statsTruncated: true }), true)).toBe("truncated");
  });

  it("source_stale -> stale (data still shown)", () => {
    expect(datasetDisplayState(descriptor({ warningCodes: ["source_stale"] }), true)).toBe("stale");
  });

  it("truncation outranks staleness when both apply", () => {
    expect(
      datasetDisplayState(descriptor({ warningCodes: ["result_truncated", "source_stale"] }), true),
    ).toBe("truncated");
  });

  it("no_data code or zero rows -> empty", () => {
    expect(datasetDisplayState(descriptor({ rows: 0, warningCodes: ["no_data"] }), true)).toBe("empty");
    expect(datasetDisplayState(descriptor({ rows: 0 }), true)).toBe("empty");
  });

  it("clean descriptor with rows -> ready", () => {
    expect(datasetDisplayState(descriptor({}), true)).toBe("ready");
    expect(datasetDisplayState(descriptor({}), true, "idle")).toBe("ready");
  });

  it("failure outranks hydration and truncation", () => {
    expect(datasetDisplayState(descriptor({ rows: 0, warningCodes: ["query_failed", "result_truncated"] }), true, "loading")).toBe("failed");
  });
});

describe("groupBannerState", () => {
  it("maps the loaded_groups sentinel values", () => {
    expect(groupBannerState(undefined)).toBe("loading");
    expect(groupBannerState(false)).toBe("loading");
    expect(groupBannerState("partial")).toBe("partial");
    expect(groupBannerState(true)).toBe("none");
  });
});
