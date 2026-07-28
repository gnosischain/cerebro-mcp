import { describe, expect, it } from "vitest";
import {
  descriptorIncomplete,
  resolveCanvasSource,
  scopeAgrees,
  type ResolveInput,
} from "../model/relationshipCanvasSource";
import type { DatasetDescriptor } from "../../shared/miniAppTypes";

function descriptor(scopeId: string, rows: unknown[][]): DatasetDescriptor {
  return {
    key: "d",
    title: "d",
    sql: "-- test",
    database: "dbt",
    columns: [],
    stats: {
      row_count: rows.length,
      rows_returned: rows.length,
      mode: "exact_bounded",
      warnings: [],
    },
    preview_rows: rows,
    scope_id: scopeId,
  } as unknown as DatasetDescriptor;
}

const NODE_ROWS = [["0xa", "address", "A", ["p"]]];
const EDGE_ROWS = [["e1", "0xa", "0xb", "p", 1, 1, true]];

function base(): ResolveInput {
  return {
    seedId: "",
    seedScope: undefined,
    seedNodeRows: undefined,
    seedEdgeRows: undefined,
    seedProfiles: [],
    seedLoading: false,
    seedError: null,
    seedControlsStale: false,

    sampleScope: undefined,
    sampleNodeRows: undefined,
    sampleEdgeRows: undefined,
    sampleNodeDescriptor: undefined,
    sampleEdgeDescriptor: undefined,
    sampleProfiles: [],
    sampleLoading: false,
    sampleError: null,
    sampleDraftStale: false,

    previewProfile: "",
    previewStateProfile: "",
    previewScope: undefined,
    previewRequestId: -1,
    desiredPreviewRequestId: 0,
    previewNodeDescriptor: undefined,
    previewEdgeDescriptor: undefined,
    previewDatasetError: null,
    previewLoading: false,
    previewError: null,

    datasetScopes: undefined,
  };
}

describe("scopeAgrees", () => {
  it("requires the scope id in every published place", () => {
    const ok = scopeAgrees(
      "s1",
      { a: "s1", b: "s1" },
      ["a", "b"],
      [{ scope_id: "s1" }, { scope_id: "s1" }],
    );
    expect(ok).toBe(true);
  });

  it("rejects an empty scope id rather than treating it as a wildcard", () => {
    expect(scopeAgrees("", { a: "" }, ["a"], [{ scope_id: "" }])).toBe(false);
  });

  it("rejects when a descriptor still carries the previous scope", () => {
    expect(
      scopeAgrees("s2", { a: "s2" }, ["a"], [{ scope_id: "s1" }]),
    ).toBe(false);
  });
});

describe("descriptorIncomplete", () => {
  it("is true while the inline page is short of the row count", () => {
    expect(descriptorIncomplete({ stats: { row_count: 10 }, preview_rows: [[1]] }))
      .toBe(true);
  });
  it("is false once the page covers the row count", () => {
    expect(descriptorIncomplete({ stats: { row_count: 1 }, preview_rows: [[1]] }))
      .toBe(false);
  });
});

describe("resolveCanvasSource", () => {
  it("reports nothing-chosen with no seed and no profiles", () => {
    const source = resolveCanvasSource(base());
    expect(source.kind).toBe("empty");
    expect(source.blocker?.reason).toBe("nothing-chosen");
  });

  it("draws the sample when every scope id agrees", () => {
    const source = resolveCanvasSource({
      ...base(),
      sampleProfiles: ["p"],
      sampleScope: { scope_id: "atlas:4" } as never,
      sampleNodeRows: NODE_ROWS,
      sampleEdgeRows: EDGE_ROWS,
      sampleNodeDescriptor: descriptor("atlas:4", NODE_ROWS),
      sampleEdgeDescriptor: descriptor("atlas:4", EDGE_ROWS),
      datasetScopes: { atlas_nodes: "atlas:4", atlas_edges: "atlas:4" },
    });
    expect(source.kind).toBe("sample");
    expect(source.blocker).toBeNull();
    expect(source.edgeRows).toHaveLength(1);
  });

  // The regression this module exists to prevent: rows are present and
  // non-empty, but they answered a PREVIOUS question. Gating on array
  // non-emptiness would render them as if they were current.
  it("refuses non-empty sample rows whose dataset_scopes entry disagrees", () => {
    const source = resolveCanvasSource({
      ...base(),
      sampleProfiles: ["p"],
      sampleScope: { scope_id: "atlas:5" } as never,
      sampleNodeRows: NODE_ROWS,
      sampleEdgeRows: EDGE_ROWS,
      sampleNodeDescriptor: descriptor("atlas:5", NODE_ROWS),
      sampleEdgeDescriptor: descriptor("atlas:5", EDGE_ROWS),
      // still pointing at the previous scope
      datasetScopes: { atlas_nodes: "atlas:4", atlas_edges: "atlas:4" },
    });
    expect(source.kind).toBe("sample");
    expect(source.blocker?.reason).toBe("stale-scope");
    expect(source.edgeRows).toHaveLength(0);
  });

  it("prefers an explicit preview over a loaded seed", () => {
    const source = resolveCanvasSource({
      ...base(),
      seedId: "0xseed",
      seedEdgeRows: EDGE_ROWS,
      previewProfile: "p",
      previewStateProfile: "p",
      previewScope: { scope_id: "pre:1", request_id: 1 } as never,
      previewRequestId: 1,
      desiredPreviewRequestId: 1,
      previewNodeDescriptor: descriptor("pre:1", NODE_ROWS),
      previewEdgeDescriptor: descriptor("pre:1", EDGE_ROWS),
      datasetScopes: {
        atlas_preview_nodes: "pre:1",
        atlas_preview_edges: "pre:1",
      },
    });
    expect(source.kind).toBe("preview");
    expect(source.blocker).toBeNull();
  });

  it("rejects a preview whose request id is behind the user's intent", () => {
    const source = resolveCanvasSource({
      ...base(),
      previewProfile: "p",
      previewStateProfile: "p",
      previewScope: { scope_id: "pre:1", request_id: 1 } as never,
      previewRequestId: 1,
      desiredPreviewRequestId: 2,
      previewNodeDescriptor: descriptor("pre:1", NODE_ROWS),
      previewEdgeDescriptor: descriptor("pre:1", EDGE_ROWS),
      datasetScopes: {
        atlas_preview_nodes: "pre:1",
        atlas_preview_edges: "pre:1",
      },
    });
    expect(source.blocker?.reason).toBe("stale-scope");
    expect(source.edgeRows).toHaveLength(0);
  });

  it("distinguishes a seed that answered emptily from one still loading", () => {
    const empty = resolveCanvasSource({
      ...base(),
      seedId: "0xseed",
      seedNodeRows: [],
      seedEdgeRows: [],
    });
    expect(empty.kind).toBe("seed");
    expect(empty.blocker?.reason).toBe("no-rows");

    const loading = resolveCanvasSource({
      ...base(),
      seedId: "0xseed",
      seedNodeRows: [],
      seedEdgeRows: [],
      seedLoading: true,
    });
    expect(loading.blocker?.reason).toBe("loading");
  });

  it("surfaces a failure instead of an empty canvas", () => {
    const source = resolveCanvasSource({
      ...base(),
      seedId: "0xseed",
      seedError: "relation unavailable",
    });
    expect(source.blocker).toEqual({
      reason: "failed",
      detail: "relation unavailable",
    });
    expect(source.stale).toBe(true);
  });
});
