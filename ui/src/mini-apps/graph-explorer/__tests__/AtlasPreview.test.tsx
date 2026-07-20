// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DatasetDescriptor } from "../../shared/miniAppTypes";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import type { GraphModel } from "../model/parseRows";
import { AtlasView, relationshipWeightUnit } from "../modes/AtlasView";
import { buildInitialState } from "../state/graphReducer";
import type {
  ForensicScope,
  GraphExplorerViewState,
  ProfileCard,
} from "../types";

vi.mock("../canvas/GraphCanvas", () => ({
  GraphCanvas: (props: {
    model: GraphModel;
    stateKey?: string;
    emptyHint?: string;
  }) => (
    <div
      data-testid="graph"
      data-edges={props.model.edgeRows.map((edge) => edge.id).join(",")}
      data-state-key={props.stateKey}
      data-empty-hint={props.emptyHint}
    />
  ),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const profile: ProfileCard = {
  profile: "token_transfers",
  model_name: "int_execution_transfers_whitelisted_daily",
  module: "transfers",
  description: "Whitelisted token transfers",
  source_kind: "address",
  target_kind: "address",
  semantic_status: "approved",
  quality_tier: "tier 1",
  question_synonyms: ["who paid whom"],
  semantic_source_file: "",
  time_aware: true,
  weight_column: "amount_usd",
};

function scope(scopeId: string, requestId: number): ForensicScope {
  return {
    scope_id: scopeId,
    request_id: requestId,
    status: "ready",
    window: {
      t0: "2026-04-20T00:00:00Z",
      t1: "2026-07-19T00:00:00Z",
      source: "atlas_preview.window_days",
    },
    data_horizon: "2026-07-18",
    sources: [
      {
        kind: "dbt_aggregate",
        name: "dbt.int_execution_transfers_whitelisted_daily",
        role: "primary",
        status: "ok",
        horizon: "2026-07-18",
        fetched_at: "2026-07-19T10:30:00Z",
      },
    ],
    coverage: {
      rows: { shown: 1, total: 1 },
      nodes: { shown: 2, total: 2 },
      edges: { shown: 1, total: 1 },
      usd: { known: null, total: null, unknown_rows: 0 },
    },
    truncation: { truncated: false, rule: "top-weight preview cap 25" },
    residuals: [],
    warnings: [],
    verification: { status: "verified", method: "source contract" },
  };
}

function serverWithPreview(
  previewScope?: ForensicScope,
): GraphExplorerViewState {
  return {
    title: "Graph Explorer",
    mode: "atlas",
    mode_revision: 0,
    catalog: [profile],
    limits: {
      max_hops: 5,
      bfs_node_cap: 2_000,
      default_expand_depth: 1,
      ui_default_window_days: 90,
      ui_default_max_neighbors: 100,
      atlas_sample_size: 150,
    },
    atlas: {
      selected_profiles: ["safe_ownership"],
      sample_size: 150,
      window_days: 90,
      scope: scope("atlas:4", 4),
    },
    atlas_preview: {
      profile: previewScope ? profile.profile : "",
      sample_size: 25,
      window_days: 90,
      scope: previewScope,
      warnings: [],
    },
    investigate: {
      seed: { id: "", kind: "" },
      active_profiles: [],
      window_days: 90,
      max_neighbors: 100,
      hops_used: 0,
    },
    selection: { node_id: "", edge_id: "", request_id: 0 },
    layout: "force",
    semantic_status_filter: "all",
    node_roles: {},
    suggested_next_hops: [],
    warnings: [],
    dataset_scopes: previewScope
      ? {
          atlas_nodes: "atlas:4",
          atlas_edges: "atlas:4",
          atlas_preview_nodes: previewScope.scope_id,
          atlas_preview_edges: previewScope.scope_id,
        }
      : { atlas_nodes: "atlas:4", atlas_edges: "atlas:4" },
  };
}

function hydrated(rows: unknown[][]): HydratedDataset {
  return {
    rows,
    columns: [],
    columnTypes: [],
    phase: "complete",
    rowsLoaded: rows.length,
    rowsExpected: rows.length,
    error: null,
    hydrating: false,
    truncated: false,
  };
}

function descriptor(
  key: string,
  scopeId: string,
  columns: string[],
  rows: unknown[][],
): DatasetDescriptor {
  return {
    key,
    title: key,
    sql: "-- test",
    database: "dbt",
    columns: columns.map((name) => ({ name, type: "String" })),
    stats: {
      row_count: rows.length,
      rows_returned: rows.length,
      mode: "exact_bounded",
      warnings: [],
    },
    preview_rows: rows,
    scope_id: scopeId,
  };
}

const appliedNodes = hydrated([
  ["0xa", "address", "A", ["safe_ownership"]],
  ["0xb", "safe", "B", ["safe_ownership"]],
]);
const appliedEdges = hydrated([
  ["applied-edge", "0xa", "0xb", "safe_ownership", 1, 1, true],
]);
const previewNodeRows = [
  ["0xc", "address", "C", [profile.profile]],
  ["0xd", "address", "D", [profile.profile]],
];
const previewEdgeRows = [
  ["preview-edge", "0xc", "0xd", profile.profile, 42, 3, true],
];

describe("Atlas catalog preview", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it("loads a real inspect-only sample, shows provenance, and applies only on Add", async () => {
    const loadPreview = vi.fn();
    const dispatch = vi.fn();
    const initialServer = serverWithPreview();
    const local = buildInitialState(initialServer);

    const render = async (
      server: GraphExplorerViewState,
      requestId: number,
      previewScope?: ForensicScope,
    ) => {
      await act(async () => {
        root.render(
          <AtlasView
            server={server}
            local={local}
            dispatch={dispatch}
            atlasNodes={appliedNodes}
            atlasEdges={appliedEdges}
            atlasPreviewNodes={previewScope ? hydrated(previewNodeRows) : undefined}
            atlasPreviewEdges={previewScope ? hydrated(previewEdgeRows) : undefined}
            atlasPreviewNodeDescriptor={
              previewScope
                ? descriptor(
                    "atlas_preview_nodes",
                    previewScope.scope_id,
                    ["id", "kind", "label", "profiles"],
                    previewNodeRows,
                  )
                : undefined
            }
            atlasPreviewEdgeDescriptor={
              previewScope
                ? descriptor(
                    "atlas_preview_edges",
                    previewScope.scope_id,
                    [
                      "id",
                      "source",
                      "target",
                      "profile",
                      "weight",
                      "edge_count",
                      "directed",
                    ],
                    previewEdgeRows,
                  )
                : undefined
            }
            loadSample={vi.fn()}
            loading={false}
            loadError={null}
            loadPreview={loadPreview}
            previewLoading={false}
            previewError={null}
            desiredPreviewRequestId={requestId}
            seedInvestigate={vi.fn()}
            onSelectNode={vi.fn()}
            onSelectEdge={vi.fn()}
            onClearSelection={vi.fn()}
          />,
        );
      });
    };

    await render(initialServer, 8);
    expect(container.querySelector("[data-testid=graph]")?.getAttribute("data-edges"))
      .toBe("applied-edge");

    await act(async () => {
      container.querySelector<HTMLButtonElement>(".ge-atlas-item")?.click();
    });
    expect(loadPreview).toHaveBeenCalledWith(profile.profile);
    expect(dispatch).not.toHaveBeenCalled();
    expect(container.textContent).toContain("Preview only");
    expect(container.querySelector("[data-testid=graph]")?.getAttribute("data-edges"))
      .toBe("");

    const acceptedScope = scope("atlas_preview:9", 9);
    await render(serverWithPreview(acceptedScope), 9, acceptedScope);

    expect(container.querySelector("[data-testid=graph]")?.getAttribute("data-edges"))
      .toBe("preview-edge");
    expect(container.querySelector("[data-testid=graph]")?.getAttribute("data-state-key"))
      .toBe("relationships:atlas:preview:token_transfers");
    expect(container.textContent).toContain("dbt.int_execution_transfers_whitelisted_daily");
    expect(container.textContent).toContain("tier 1");
    expect(container.textContent).toContain("USD value");
    expect(container.textContent).toContain("Edges: 1 of 1");
    expect(container.textContent).toContain("2026-07-18");
    expect(container.textContent).toContain("primary · ok");

    await act(async () => {
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent?.includes("Add to graph"))
        ?.click();
    });
    expect(dispatch).toHaveBeenCalledWith({
      type: "TOGGLE_ATLAS_PROFILE",
      profile: profile.profile,
    });
    expect(container.querySelector("[data-testid=graph]")?.getAttribute("data-edges"))
      .toBe("applied-edge");
  });

  it("rejects an older same-profile scope and exposes preview failures without stale rows", async () => {
    const oldScope = scope("atlas_preview:11", 11);
    const server = serverWithPreview(oldScope);
    const local = buildInitialState(serverWithPreview());
    const loadPreview = vi.fn();

    const render = async (error: string | null) => {
      await act(async () => {
        root.render(
          <AtlasView
            server={server}
            local={local}
            dispatch={vi.fn()}
            atlasNodes={appliedNodes}
            atlasEdges={appliedEdges}
            atlasPreviewNodes={hydrated(previewNodeRows)}
            atlasPreviewEdges={hydrated(previewEdgeRows)}
            atlasPreviewNodeDescriptor={descriptor(
              "atlas_preview_nodes",
              oldScope.scope_id,
              ["id", "kind", "label", "profiles"],
              previewNodeRows,
            )}
            atlasPreviewEdgeDescriptor={descriptor(
              "atlas_preview_edges",
              oldScope.scope_id,
              ["id", "source", "target", "profile", "weight", "edge_count", "directed"],
              previewEdgeRows,
            )}
            loadSample={vi.fn()}
            loading={false}
            loadError={null}
            loadPreview={loadPreview}
            previewLoading={!error}
            previewError={error}
            desiredPreviewRequestId={12}
            seedInvestigate={vi.fn()}
            onSelectNode={vi.fn()}
            onSelectEdge={vi.fn()}
            onClearSelection={vi.fn()}
          />,
        );
      });
    };

    await render(null);
    await act(async () => {
      container.querySelector<HTMLButtonElement>(".ge-atlas-item")?.click();
    });
    expect(container.querySelector("[data-testid=graph]")?.getAttribute("data-edges"))
      .toBe("");
    expect(container.textContent).toContain("loading real sample");

    await render("source unavailable");
    expect(container.querySelector("[data-testid=graph]")?.getAttribute("data-edges"))
      .toBe("");
    expect(container.textContent).toContain("Preview failed: source unavailable");
    expect(container.textContent).not.toContain("Edges: 1 of 1");
    expect(
      [...container.querySelectorAll("button")].some(
        (button) => button.textContent === "Retry preview",
      ),
    ).toBe(true);
  });

  it("derives analyst-readable relationship weight units", () => {
    expect(relationshipWeightUnit(null)).toBe(
      "Unweighted relationship (edge count)",
    );
    expect(relationshipWeightUnit("transfer_count")).toBe("Count");
    expect(relationshipWeightUnit("amount_raw")).toBe(
      "Token amount (native units)",
    );
  });
});
