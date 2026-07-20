// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { buildCaseExportFiles, buildStoredZip } from "../caseExport";
import type { GraphExplorerViewState } from "../types";

describe("forensic case export", () => {
  it("exports scopes, complete hydrated rows, queries, hashes, and limitations", () => {
    const scope = {
      scope_id: "flows:7:test",
      request_id: 7,
      status: "partial" as const,
      window: { t0: "2026-01-01", t1: "2026-02-01", source: "test" },
      data_horizon: "2026-02-01",
      sources: [],
      coverage: {
        rows: { shown: 1, total: null },
        nodes: { shown: 2, total: null },
        edges: { shown: 1, total: null },
        usd: { known: 1, total: null, unknown_rows: 0 },
      },
      truncation: { truncated: false, rule: null },
      residuals: ["Native value is outside this contract."],
      warnings: [],
      verification: { status: "unverified", method: null },
    };
    const server = {
      mode: "flows",
      selection: { node_id: "", edge_id: "", request_id: 0 },
      flows: { scope },
    } as unknown as GraphExplorerViewState;
    const files = buildCaseExportFiles({
      viewId: "view-1",
      server,
      datasets: {
        flow_edges: {
          rows: [["e1", "0xa", "0xb"]],
          columns: ["id", "source", "target"],
          columnTypes: ["String", "String", "String"],
          phase: "complete",
          rowsLoaded: 1,
          rowsExpected: 1,
          error: null,
          hydrating: false,
          truncated: false,
        },
      },
      descriptors: {
        flow_edges: {
          key: "flow_edges",
          title: "Edges",
          sql: "SELECT * FROM evidence",
          database: "dbt",
          columns: [],
          stats: { row_count: 1, rows_returned: 1, mode: "exact_bounded", warnings: [] },
          preview_rows: [],
        },
      },
      analyst: "Ada",
      now: new Date("2026-07-19T12:00:00Z"),
    });
    const paths = files.map((file) => file.path);
    expect(paths).toContain("case.json");
    expect(paths).toContain("forensic-scopes/flows_7_test.json");
    expect(paths).toContain("datasets/flow_edges.csv");
    expect(paths).toContain("queries/flow_edges.sql");
    expect(paths).toContain("limitations.md");
    expect(paths).toContain("manifest.sha256");
    expect(String(files.find((file) => file.path === "case.json")?.content)).toContain('"analyst": "Ada"');
    expect(String(files.find((file) => file.path === "limitations.md")?.content)).toContain("Native value");
  });

  it("builds a valid ZIP envelope containing deterministic filenames", () => {
    const zip = buildStoredZip([
      { path: "case.json", content: "{}\n" },
      { path: "datasets/a.csv", content: "a\r\n1\r\n" },
    ]);
    expect(Array.from(zip.slice(0, 4))).toEqual([0x50, 0x4b, 0x03, 0x04]);
    expect(new TextDecoder().decode(zip)).toContain("case.json");
    expect(new TextDecoder().decode(zip)).toContain("datasets/a.csv");
    expect(Array.from(zip.slice(-22, -18))).toEqual([0x50, 0x4b, 0x05, 0x06]);
  });
});
