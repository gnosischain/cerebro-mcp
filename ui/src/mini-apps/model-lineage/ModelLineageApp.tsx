import { useEffect, useMemo, useState } from "react";
import type { MiniAppPayload } from "../shared/miniAppTypes";
import { useMiniApp } from "../shared/useMiniApp";
import { WarningBanner } from "../shared/WarningBanner";
import { MiniAppChrome } from "../shared/MiniAppChrome";
import { MaHelpButton } from "../shared/HelpDialog";
import { MODEL_LINEAGE_HELP } from "../shared/helpContent";
import { FilterBar } from "./FilterBar";
import { LineageGraph } from "./LineageGraph";
import { DetailsPanel } from "./DetailsPanel";
import { ColumnLineageDrawer } from "./ColumnLineageDrawer";
import { CatalogScreen } from "./CatalogScreen";
import type {
  CatalogEntry,
  LineageDirection,
  LineageLayer,
  ModelLineageState,
} from "./types";

const APP_ID = "model_lineage";

const EMPTY_STATE: ModelLineageState = {
  title: "Model Lineage Explorer",
  seed: "",
  seed_id: "",
  layer: "model",
  direction: "both",
  depth: 1,
  include_kinds: [],
  tags_filter: [],
  selected_node_id: "",
  selected_column: "",
  catalog: [],
  warnings: [],
};

const DEV_CATALOG: CatalogEntry[] = [
  { name: "fct_execution_pools_daily", schema: "marts", materialized: "incremental", tags: ["pools", "daily"], description: "Daily DEX pool metrics" },
  { name: "int_execution_transfers", schema: "intermediate", materialized: "view", tags: ["transfers"], description: "Decoded ERC20 transfers" },
  { name: "api_execution_pools_overview", schema: "api", materialized: "table", tags: ["pools", "api"], description: "Public pools API model" },
  { name: "stg_consensus__blocks", schema: "staging", materialized: "view", tags: ["consensus"], description: "Staging consensus blocks" },
];

// ---------------------------------------------------------------------------
// Dev-only fixture (Vite without an MCP host). Column orders mirror the Python
// NODES_COLUMNS / EDGES_COLUMNS / COLUMN_EDGES_COLUMNS.
// ---------------------------------------------------------------------------

const DEV_NODES: unknown[][] = [
  ["source.gnosis_dbt.raw.execution_logs", "execution_logs", "source", "", "raw", [], "Raw EVM logs", 12, 0],
  ["model.gnosis_dbt.int_execution_transfers", "int_execution_transfers", "model", "view", "intermediate", ["transfers"], "Decoded ERC20 transfers", 9, 3],
  ["model.gnosis_dbt.fct_execution_pools_daily", "fct_execution_pools_daily", "model", "incremental", "marts", ["pools", "daily"], "Daily DEX pool metrics", 18, 6],
  ["model.gnosis_dbt.int_execution_pools_events", "int_execution_pools_events", "model", "view", "intermediate", ["pools"], "Pool liquidity events", 14, 2],
  ["model.gnosis_dbt.api_execution_pools_overview", "api_execution_pools_overview", "model", "table", "api", ["pools", "api"], "Public pools API model", 22, 4],
];

const DEV_EDGES: unknown[][] = [
  ["e1", "source.gnosis_dbt.raw.execution_logs", "model.gnosis_dbt.int_execution_transfers", "model"],
  ["e2", "model.gnosis_dbt.int_execution_transfers", "model.gnosis_dbt.int_execution_pools_events", "model"],
  ["e3", "model.gnosis_dbt.int_execution_pools_events", "model.gnosis_dbt.fct_execution_pools_daily", "model"],
  ["e4", "model.gnosis_dbt.fct_execution_pools_daily", "model.gnosis_dbt.api_execution_pools_overview", "model"],
];

const DEV_COLUMN_EDGES: unknown[][] = [
  ["c1", "int_execution_pools_events", "amount_usd", "fct_execution_pools_daily", "volume_usd", "column"],
  ["c2", "int_execution_transfers", "value", "int_execution_pools_events", "amount_usd", "column"],
];

const MOCK_PAYLOAD: MiniAppPayload<ModelLineageState> = {
  type: "INITIAL_LOAD",
  view_id: "dev-view",
  app_id: APP_ID,
  title: "Model Lineage Explorer",
  status: "ready",
  summary_cards: [],
  datasets: {
    nodes: {
      key: "nodes",
      title: "Models",
      columns: [] as never,
      preview_rows: DEV_NODES,
      page_token: "",
    } as never,
    edges: {
      key: "edges",
      title: "Lineage Edges",
      columns: [] as never,
      preview_rows: DEV_EDGES,
      page_token: "",
    } as never,
    column_edges: {
      key: "column_edges",
      title: "Column Lineage",
      columns: [] as never,
      preview_rows: DEV_COLUMN_EDGES,
      page_token: "",
    } as never,
  },
  view_state: {
    ...EMPTY_STATE,
    seed: "fct_execution_pools_daily",
    seed_id: "model.gnosis_dbt.fct_execution_pools_daily",
    selected_node_id: "model.gnosis_dbt.fct_execution_pools_daily",
    depth: 2,
    catalog: DEV_CATALOG,
  },
  warnings: [],
};

export default function ModelLineageApp() {
  const { view, callTool } = useMiniApp<ModelLineageState>({
    appId: APP_ID,
    mockPayload: MOCK_PAYLOAD,
  });

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detailsCollapsed, setDetailsCollapsed] = useState(false);
  // Node selection is a pure-UI concern — there is no backend "select only"
  // tool, and re-querying the subgraph on every click would be wasteful. Track
  // it locally; fall back to the backend's selected_node_id / seed on first
  // render and after a fresh load.
  const [localSelected, setLocalSelected] = useState<string>("");

  const state = (view?.view_state ?? EMPTY_STATE) as ModelLineageState;

  const nodeCount = view?.datasets?.nodes?.preview_rows?.length ?? 0;
  const edgeCount = view?.datasets?.edges?.preview_rows?.length ?? 0;

  const seedId = state.seed_id || "";
  const selectedNodeId = localSelected || state.selected_node_id || seedId;

  // A fresh load / recenter changes the seed — drop the stale local selection
  // so the new seed node is the one highlighted.
  useEffect(() => {
    setLocalSelected("");
  }, [seedId]);

  const onSeed = (model: string) => {
    void callTool("open_model_lineage", {
      seed_model: model,
      direction: state.direction,
      depth: state.depth,
      layer: state.layer,
    }).catch((err) => console.error("[model_lineage] seed failed", err));
  };

  const onBrowse = () => {
    // Re-open with no seed → fresh view with empty graph + catalog, which
    // routes back to the browse/search start screen.
    void callTool("open_model_lineage", {}).catch((err) =>
      console.error("[model_lineage] browse failed", err),
    );
  };

  const onLayerChange = (layer: LineageLayer) => {
    if (!view?.view_id) return;
    void callTool("set_model_lineage_filters", {
      view_id: view.view_id,
      layer,
    }).catch((err) => console.error("[model_lineage] layer change failed", err));
  };

  const onDirectionChange = (direction: LineageDirection) => {
    if (!view?.view_id) return;
    void callTool("set_model_lineage_filters", {
      view_id: view.view_id,
      direction,
    }).catch((err) => console.error("[model_lineage] direction change failed", err));
  };

  const onDepthChange = (depth: number) => {
    if (!view?.view_id) return;
    void callTool("set_model_lineage_filters", {
      view_id: view.view_id,
      depth,
    }).catch((err) => console.error("[model_lineage] depth change failed", err));
  };

  const onSelectNode = (id: string) => {
    setLocalSelected(id);
  };

  const onExpandNode = (id: string) => {
    if (!view?.view_id) return;
    void callTool("expand_model_lineage_node", {
      view_id: view.view_id,
      node_id: id,
      direction: state.direction,
      depth: 1,
    }).catch((err) => console.error("[model_lineage] expand failed", err));
  };

  const onRecenter = (modelName: string) => {
    void callTool("open_model_lineage", {
      seed_model: modelName,
      direction: state.direction,
      depth: state.depth,
      layer: state.layer,
    }).catch((err) => console.error("[model_lineage] recenter failed", err));
  };

  const onTraceColumn = (modelName: string, column: string) => {
    if (!view?.view_id) return;
    setDrawerOpen(true);
    void callTool("load_column_lineage", {
      view_id: view.view_id,
      model_name: modelName,
      column,
      direction: "upstream",
      depth: 1,
    }).catch((err) => console.error("[model_lineage] trace column failed", err));
  };

  const selectedModelName = useMemo(() => {
    const rows = view?.datasets?.nodes?.preview_rows ?? [];
    const match = rows.find((r) => String(r[0]) === selectedNodeId);
    return match ? String(match[1]) : "";
  }, [view, selectedNodeId]);

  if (!view) {
    return (
      <MiniAppChrome activeTabId="lineage" rightSlot={<MaHelpButton content={MODEL_LINEAGE_HELP} />}>
        <div className="ma-empty">Loading Model Lineage Explorer…</div>
      </MiniAppChrome>
    );
  }

  // No graph loaded yet → show the browse/search start screen so users can
  // discover a seed model without knowing its exact name.
  if (!state.seed && nodeCount === 0) {
    return (
      <MiniAppChrome activeTabId="lineage" bodyClassName="ma-body--flush" rightSlot={<MaHelpButton content={MODEL_LINEAGE_HELP} />}>
        <div className="ml-shell">
          <WarningBanner warnings={view.warnings ?? []} />
          <CatalogScreen catalog={state.catalog ?? []} onSeed={onSeed} />
        </div>
      </MiniAppChrome>
    );
  }

  return (
    <MiniAppChrome activeTabId="lineage" bodyClassName="ma-body--flush" rightSlot={<MaHelpButton content={MODEL_LINEAGE_HELP} />}>
      <div className="ml-shell">
        <WarningBanner warnings={view.warnings ?? []} />
        <FilterBar
          state={state}
          onSeed={onSeed}
          onLayerChange={onLayerChange}
          onDirectionChange={onDirectionChange}
          onDepthChange={onDepthChange}
          onBrowse={onBrowse}
          nodeCount={nodeCount}
          edgeCount={edgeCount}
        />
        <div className="ml-body">
          <main className="ml-canvas">
            <LineageGraph
              nodes={view.datasets?.nodes}
              edges={view.datasets?.edges}
              seedId={seedId}
              selectedNodeId={selectedNodeId}
              onSelectNode={onSelectNode}
              onExpandNode={onExpandNode}
            />
          </main>
          <DetailsPanel
            nodes={view.datasets?.nodes}
            selectedNodeId={selectedNodeId}
            collapsed={detailsCollapsed}
            onToggleCollapse={() => setDetailsCollapsed((v) => !v)}
            onExpand={onExpandNode}
            onRecenter={onRecenter}
            onTraceColumn={onTraceColumn}
          />
        </div>
        <ColumnLineageDrawer
          columnEdges={view.datasets?.column_edges}
          selectedColumn={state.selected_column}
          modelName={selectedModelName}
          open={drawerOpen && Boolean(state.selected_column)}
          onClose={() => setDrawerOpen(false)}
        />
      </div>
    </MiniAppChrome>
  );
}
