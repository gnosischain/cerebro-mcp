import { useEffect, useMemo, useRef, useState } from "react";
import type { MiniAppPayload } from "../shared/miniAppTypes";
import { useMiniApp } from "../shared/useMiniApp";
import { WarningBanner } from "../shared/WarningBanner";
import { CatalogScreen } from "./CatalogScreen";
import { DetailsPanel } from "./DetailsPanel";
import { FilterBar } from "./FilterBar";
import { ForceGraph } from "./ForceGraph";
import type { GraphExplorerState, ProfileCard } from "./types";

const APP_ID = "graph_explorer";

const EMPTY_STATE: GraphExplorerState = {
  title: "Graph Explorer",
  catalog: [],
  selected_profiles: [],
  seed_node: { id: "", kind: "" },
  selected_node_id: "",
  selected_edge_id: "",
  relation_types: [],
  layout: "force",
  transfer_window_days: 90,
  max_neighbors: 25,
  hops: 0,
  semantic_status_filter: "all",
  suggested_next_hops: [],
  node_roles: {},
  warnings: [],
};

// Dev-only fixture used when the mini-app runs in Vite without an MCP host.
// Production reads the real payload from <script id="mini-app-data">; this is
// never seen in the bundled build when the host injects data.
const DEV_CATALOG: ProfileCard[] = [
  { profile: "circles_trust", model_name: "execution_circles_v2_trust_relations_current", module: "Circles", description: "Circles v2 trust graph", source_kind: "circles_avatar", target_kind: "circles_avatar", semantic_status: "approved", quality_tier: "approved", question_synonyms: ["trust graph"], semantic_source_file: "", time_aware: true },
  { profile: "circles_avatar_balances", model_name: "fct_execution_circles_v2_avatar_balances_latest", module: "Circles", description: "Avatar holds CRC token", source_kind: "circles_avatar", target_kind: "token", semantic_status: "approved", quality_tier: "approved", question_synonyms: [], semantic_source_file: "", time_aware: false },
  { profile: "safe_ownership", model_name: "int_execution_safes_current_owners", module: "safe", description: "Who owns each Safe", source_kind: "address", target_kind: "safe", semantic_status: "candidate", quality_tier: "candidate", question_synonyms: ["safe owners"], semantic_source_file: "", time_aware: false },
  { profile: "gpay_ownership", model_name: "int_execution_gpay_wallet_owners", module: "gpay", description: "GPay wallet owners", source_kind: "address", target_kind: "gpay_wallet", semantic_status: "candidate", quality_tier: "candidate", question_synonyms: [], semantic_source_file: "", time_aware: true },
  { profile: "token_transfers", model_name: "int_execution_transfers_whitelisted_daily", module: "transfers", description: "Whitelisted ERC20 transfers", source_kind: "address", target_kind: "address", semantic_status: "candidate", quality_tier: "candidate", question_synonyms: [], semantic_source_file: "", time_aware: true },
  { profile: "lp_in_pool", model_name: "int_execution_pools_dex_liquidity_events", module: "pools", description: "LP provider → pool", source_kind: "address", target_kind: "pool", semantic_status: "approved", quality_tier: "approved", question_synonyms: [], semantic_source_file: "", time_aware: true },
  { profile: "pool_contains_token", model_name: "int_execution_pools_dex_liquidity_events", module: "pools", description: "Pool contains token", source_kind: "pool", target_kind: "token", semantic_status: "approved", quality_tier: "approved", question_synonyms: [], semantic_source_file: "", time_aware: false },
  { profile: "deposit_to_validator", model_name: "int_GBCDeposit_deposists_daily", module: "GBCDeposit", description: "Deposit → validator", source_kind: "address", target_kind: "validator", semantic_status: "approved", quality_tier: "approved", question_synonyms: [], semantic_source_file: "", time_aware: true },
  { profile: "bridge_user_flows", model_name: "int_execution_bridges_address_flows_daily", module: "bridges", description: "User ↔ bridge", source_kind: "address", target_kind: "bridge", semantic_status: "candidate", quality_tier: "candidate", question_synonyms: [], semantic_source_file: "", time_aware: true },
];

const DEV_NODES: unknown[][] = [
  ["0xaaa", "address", "0xaaa…1", ["token_transfers", "safe_ownership"]],
  ["0xbbb", "safe", "0xbbb…2", ["safe_ownership"]],
  ["0xccc", "circles_avatar", "0xccc…3", ["circles_trust", "circles_avatar_balances"]],
  ["0xddd", "token", "0xddd…4 CRC", ["circles_avatar_balances", "token_transfers"]],
  ["0xeee", "pool", "0xeee…5 B-V2", ["lp_in_pool", "pool_contains_token"]],
  ["0xfff", "address", "0xfff…6", ["lp_in_pool", "token_transfers"]],
  ["0x111", "validator", "validator #4096", ["deposit_to_validator"]],
  ["0x222", "address", "0x222…8", ["token_transfers", "gpay_ownership"]],
  ["0x333", "gpay_wallet", "0x333…9", ["gpay_ownership"]],
  ["0x444", "bridge", "0x444…a xDai", ["bridge_user_flows"]],
  ["0x555", "address", "0x555…b", ["token_transfers"]],
  ["0x666", "token", "0x666…c USDC", ["token_transfers", "pool_contains_token"]],
];

const DEV_EDGES: unknown[][] = [
  ["e1", "0xaaa", "0xbbb", "safe_ownership", 1, 1, true],
  ["e2", "0xaaa", "0xddd", "token_transfers", 1500, 12, true],
  ["e3", "0xccc", "0xddd", "circles_avatar_balances", 820, 1, true],
  ["e4", "0xccc", "0xaaa", "circles_trust", 1, 1, true],
  ["e5", "0xfff", "0xeee", "lp_in_pool", 50000, 3, true],
  ["e6", "0xeee", "0xddd", "pool_contains_token", 1, 1, false],
  ["e7", "0x111", "0x111", "deposit_to_validator", 32, 1, true],
  ["e8", "0x222", "0x333", "gpay_ownership", 1, 1, true],
  ["e9", "0x222", "0x444", "bridge_user_flows", 2500, 5, true],
  ["e10", "0x333", "0xddd", "token_transfers", 300, 2, true],
  ["e11", "0xbbb", "0xeee", "token_transfers", 4200, 7, true],
  ["e12", "0x555", "0xaaa", "token_transfers", 180, 4, true],
  ["e13", "0xeee", "0x666", "pool_contains_token", 1, 1, false],
  ["e14", "0xfff", "0x666", "token_transfers", 9800, 20, true],
];

// Dev-only: `sessionStorage.ge_force_empty = '1'` shows the catalog/empty state
// even when the seeded mock fixture is defined, so we can iterate on both screens
// without editing source each time.
// `sessionStorage.ge_force_sample = '1'` shows sample mode (no seed, edges loaded).
const DEV_FORCE_EMPTY =
  typeof window !== "undefined" && window.sessionStorage?.getItem("ge_force_empty") === "1";
const DEV_FORCE_SAMPLE =
  typeof window !== "undefined" && window.sessionStorage?.getItem("ge_force_sample") === "1";

const MOCK_PAYLOAD: MiniAppPayload<GraphExplorerState> = {
  type: "INITIAL_LOAD",
  view_id: "dev-view",
  app_id: APP_ID,
  title: "Graph Explorer",
  status: "ready",
  summary_cards: [],
  datasets: {
    nodes: {
      name: "nodes",
      columns: [{ name: "id" }, { name: "kind" }, { name: "label" }, { name: "profiles" }] as unknown as never,
      preview_rows: DEV_NODES,
      page_token: "",
    } as never,
    edges: {
      name: "edges",
      columns: [
        { name: "id" }, { name: "source" }, { name: "target" },
        { name: "profile" }, { name: "weight" }, { name: "edge_count" }, { name: "directed" },
      ] as unknown as never,
      preview_rows: DEV_EDGES,
      page_token: "",
    } as never,
  },
  view_state: DEV_FORCE_EMPTY
    ? { ...EMPTY_STATE, catalog: DEV_CATALOG }
    : DEV_FORCE_SAMPLE
    ? {
        ...EMPTY_STATE,
        catalog: DEV_CATALOG,
        mode: "sample",
        selected_profiles: ["gpay_ownership"],
        relation_types: ["gpay_ownership"],
      }
    : {
    ...EMPTY_STATE,
    catalog: DEV_CATALOG,
    seed_node: { id: "0xaaa", kind: "address" },
    selected_node_id: "0xccc",
    selected_profiles: [
      "circles_trust", "circles_avatar_balances", "safe_ownership",
      "token_transfers", "lp_in_pool", "pool_contains_token",
    ],
    relation_types: [
      "circles_trust", "circles_avatar_balances", "safe_ownership",
      "token_transfers", "lp_in_pool", "pool_contains_token",
    ],
    hops: 1,
    node_roles: {
      "0xaaa": { is_safe: 0, is_gpay_wallet: 0, is_ga_user: 0, is_circles_avatar: 0, is_safe_owner: 1, is_lp_provider: 0, has_dune_label: 1, dune_project: "GnosisDAO" } as never,
      "0xccc": { is_safe: 0, is_circles_avatar: 1, circles_avatar_type: "Human", is_safe_owner: 0, has_dune_label: 0 } as never,
    },
    suggested_next_hops: [
      { profile: "bridge_user_flows", label: "Bridge flows", rationale: "addr ↔ bridge", quality_tier: "candidate" },
    ],
  },
  warnings: [],
};

const SECTOR_LABELS: Record<string, string> = {
  Circles: "Circles",
  circles: "Circles",
  gpay: "GPay",
  safe: "Safe",
  transfers: "Transfers",
  pools: "Pools",
  yields: "Yields",
  consensus: "Staking",
  GBCDeposit: "Staking",
  bridges: "Bridges",
  crawlers_data: "Labels",
  shared: "Shared",
};

function sectorOf(module: string): string {
  return SECTOR_LABELS[module] ?? module ?? "Other";
}

function groupProfilesBySector(
  profiles: ProfileCard[],
): Array<[string, ProfileCard[]]> {
  const out: Record<string, ProfileCard[]> = {};
  for (const profile of profiles) {
    const sector = sectorOf(profile.module);
    (out[sector] ||= []).push(profile);
  }
  return Object.entries(out).sort(([a], [b]) => a.localeCompare(b));
}

export default function GraphExplorerApp() {
  const { view, callTool, updateModelContext, sendMessage } =
    useMiniApp<GraphExplorerState>({ appId: APP_ID, mockPayload: MOCK_PAYLOAD });

  // Optimistic view-only overrides. Chip toggle, layout change, and status
  // filter are pure-UI operations — they don't need a backend round-trip to
  // take effect. We apply locally, then fire-and-forget the server update
  // so the persisted view reflects the user's choice.
  const [optimistic, setOptimistic] = useState<Partial<GraphExplorerState>>({});
  // Clear any optimistic override as soon as the server echoes the same
  // field back via view.view_state, so stale overrides never outlive the truth.
  useEffect(() => {
    if (!view) return;
    setOptimistic((cur) => {
      const base = view.view_state as GraphExplorerState | undefined;
      if (!base) return cur;
      let next = cur;
      for (const key of Object.keys(cur) as (keyof GraphExplorerState)[]) {
        if (JSON.stringify(cur[key]) === JSON.stringify(base[key])) {
          if (next === cur) next = { ...cur };
          delete (next as Partial<GraphExplorerState>)[key];
        }
      }
      return next;
    });
  }, [view]);

  const baseState = (view?.view_state ?? EMPTY_STATE) as GraphExplorerState;
  const state = useMemo<GraphExplorerState>(
    () => ({ ...baseState, ...optimistic }),
    [baseState, optimistic],
  );
  const sectors = useMemo(() => groupProfilesBySector(state.catalog || []), [state.catalog]);
  // Show the graph screen whenever we have a real seed OR sample edges loaded.
  const edgesLoaded = (view?.datasets?.edges?.preview_rows?.length ?? 0) > 0;
  const hasSubgraph = Boolean(state.seed_node?.id) || edgesLoaded;
  const isEmpty = !hasSubgraph;
  const isSampleMode = !state.seed_node?.id && edgesLoaded;

  // Details panel: open by default on wide viewports, closed on narrow
  // (overlay style) so the graph canvas owns the entire visible area.
  const [detailsOpen, setDetailsOpen] = useState<boolean>(() =>
    typeof window === "undefined" ? true : window.innerWidth > 900,
  );
  // When a node is selected on narrow viewports, auto-open the overlay.
  useEffect(() => {
    if (!state.selected_node_id) return;
    if (typeof window !== "undefined" && window.innerWidth <= 900) {
      setDetailsOpen(true);
    }
  }, [state.selected_node_id]);

  const onSeed = (profileId: string | null, nodeId: string) => {
    if (!view?.view_id) return;
    void callTool("load_graph_explorer_seed", {
      view_id: view.view_id,
      seed_node_id: nodeId,
      seed_model: profileId ?? "",
      relation_types: profileId ? [profileId] : [],
      hops: 1,
      transfer_window_days: state.transfer_window_days,
      max_neighbors: state.max_neighbors,
    }).catch((err) => {
      console.error("[graph_explorer] seed failed", err);
    });
  };

  /**
   * Reload the subgraph around the CURRENT seed with fresh filters.
   * Used when the user changes window/max/relation_types and expects the
   * backend to re-query with those settings.
   */
  const refetch = (overrides: Partial<GraphExplorerState> = {}) => {
    if (!view?.view_id || !state.seed_node?.id) return;
    const merged = { ...state, ...overrides };
    void callTool("load_graph_explorer_seed", {
      view_id: view.view_id,
      seed_node_id: state.seed_node.id,
      seed_model: "",
      relation_types: merged.relation_types,
      hops: 1,
      transfer_window_days: merged.transfer_window_days,
      max_neighbors: merged.max_neighbors,
    }).catch((err) => console.error("[graph_explorer] refetch failed", err));
  };

  const onExpand = (nodeId: string) => {
    if (!view?.view_id || !nodeId) return;
    void callTool("expand_graph_explorer_node", {
      view_id: view.view_id,
      node_id: nodeId,
      relation_types: state.relation_types,
      direction: "both",
      hops: 1,
    }).catch((err) => console.error("[graph_explorer] expand failed", err));
  };

  // Debounce window/max-neighbors refetches so typing doesn't spam the server.
  const refetchTimerRef = useRef<number | null>(null);
  const scheduleRefetch = (overrides: Partial<GraphExplorerState>) => {
    if (refetchTimerRef.current) window.clearTimeout(refetchTimerRef.current);
    refetchTimerRef.current = window.setTimeout(() => {
      refetch(overrides);
      refetchTimerRef.current = null;
    }, 600);
  };

  const onFocus = (patch: Partial<GraphExplorerState>) => {
    // 1. Optimistic local merge — immediate visual feedback.
    setOptimistic((cur) => ({ ...cur, ...patch }));

    if (!view?.view_id) return;

    // 2. Pure view-only changes: persist via update_graph_explorer_focus (no refetch).
    const viewOnly: Record<string, unknown> = { view_id: view.view_id };
    if (patch.selected_node_id) viewOnly.selected_node_id = patch.selected_node_id;
    if (patch.selected_edge_id) viewOnly.selected_edge_id = patch.selected_edge_id;
    if (patch.layout) viewOnly.layout = patch.layout;
    if (patch.semantic_status_filter)
      viewOnly.semantic_status_filter = patch.semantic_status_filter;
    if (Object.keys(viewOnly).length > 1) {
      void callTool("update_graph_explorer_focus", viewOnly).catch(() => {});
    }

    // 3. Data-affecting changes: refetch the subgraph.
    // relation_types change → refetch if we added a profile whose edges aren't
    // already loaded (removing is just a client filter — no refetch needed).
    if (patch.relation_types && state.seed_node?.id) {
      const existing = new Set(state.selected_profiles || state.relation_types);
      const adding = patch.relation_types.some((p) => !existing.has(p));
      if (adding) {
        refetch({ relation_types: patch.relation_types });
      } else {
        // shrinking — just persist the choice
        void callTool("update_graph_explorer_focus", {
          view_id: view.view_id,
          relation_types: patch.relation_types,
        }).catch(() => {});
      }
    }
    if (patch.transfer_window_days !== undefined) {
      scheduleRefetch({ transfer_window_days: patch.transfer_window_days });
    }
    if (patch.max_neighbors !== undefined) {
      scheduleRefetch({ max_neighbors: patch.max_neighbors });
    }
  };

  const toggleProfile = (profileId: string) => {
    const current = new Set(state.relation_types);
    if (current.has(profileId)) current.delete(profileId);
    else current.add(profileId);
    onFocus({ relation_types: Array.from(current) });
  };

  /** Expand the seed node — gives the user a one-click "more hops" action. */
  const onExpandAll = () => {
    if (!state.seed_node?.id) return;
    onExpand(state.seed_node.id);
  };

  const onAskAssistant = () => {
    updateModelContext({
      view_id: view?.view_id ?? "",
      seed: state.seed_node?.id,
      selected_node: state.selected_node_id,
      profiles: state.relation_types,
      window_days: state.transfer_window_days,
      node_kinds: Array.from(
        new Set(
          Object.values(state.node_roles || {})
            .map((r) => (r?.is_circles_avatar ? "circles_avatar" : ""))
            .filter(Boolean),
        ),
      ),
    });
    void sendMessage(
      `Summarize the current Graph Explorer subgraph (seed ${state.seed_node?.id}, ` +
        `${state.relation_types.length} active profiles) and highlight anomalies.`,
    );
  };

  const onReset = () => {
    if (!view?.view_id) return;
    void callTool("open_graph_explorer", {}).catch((err) =>
      console.error("[graph_explorer] reset failed", err),
    );
  };

  if (!view) return <div className="ge-shell">Loading…</div>;

  const activeSet = new Set(state.relation_types);
  const statusFilter = state.semantic_status_filter;

  return (
    <div className="ge-shell">
      <WarningBanner warnings={view.warnings ?? []} />
      {isEmpty ? (
        <CatalogScreen catalog={state.catalog || []} onSeed={onSeed} />
      ) : (
        <>
          <FilterBar
            view={state}
            onFocus={onFocus}
            onAskAssistant={onAskAssistant}
            onReset={onReset}
            detailsOpen={detailsOpen}
            onToggleDetails={() => setDetailsOpen((v) => !v)}
            onExpand={onExpandAll}
            isSampleMode={isSampleMode}
          />
          <nav className="ge-chip-strip">
            {sectors.map(([sector, profiles]) => {
              const visible = profiles.filter((p) =>
                statusFilter === "all" ? true : p.semantic_status === statusFilter,
              );
              if (!visible.length) return null;
              const ids = visible.map((p) => p.profile);
              const allOn = ids.every((id) => activeSet.has(id));
              return (
                <div key={sector} className="ge-chip-group">
                  <button
                    type="button"
                    className={`ge-chip-group-label ${allOn ? "all-on" : ""}`}
                    onClick={() => {
                      const next = new Set(state.relation_types);
                      if (allOn) ids.forEach((id) => next.delete(id));
                      else ids.forEach((id) => next.add(id));
                      onFocus({ relation_types: Array.from(next) });
                    }}
                    title={`Toggle all ${visible.length} ${sector} profile(s)`}
                  >
                    {sector}
                  </button>
                  {visible.map((p) => {
                    const active = activeSet.has(p.profile);
                    return (
                      <button
                        key={p.profile}
                        type="button"
                        className={`ge-chip ${active ? "active" : ""} ${p.semantic_status}`}
                        onClick={() => toggleProfile(p.profile)}
                        title={`${p.profile} — ${p.description || ""}\n${p.source_kind} → ${p.target_kind}`}
                      >
                        <span className="ge-chip-dot" aria-hidden />
                        <span className="ge-chip-name">{p.profile}</span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </nav>
          <div className={`ge-body ${detailsOpen ? "details-open" : "details-closed"}`}>
            <main className="ge-canvas">
              <ForceGraph
                nodes={view.datasets?.nodes}
                edges={view.datasets?.edges}
                selectedNodeId={state.selected_node_id}
                selectedEdgeId={state.selected_edge_id}
                activeProfiles={state.relation_types}
                layout={state.layout}
                onSelectNode={(id) => onFocus({ selected_node_id: id })}
                onSelectEdge={(id) => onFocus({ selected_edge_id: id })}
                onExpandNode={onExpand}
              />
            </main>
            <DetailsPanel
              view={view}
              onExpand={onExpand}
              onRecenter={(id) => onSeed(null, id)}
              onApplyHop={(profileId) => {
                const current = new Set(state.relation_types);
                current.add(profileId);
                onFocus({ relation_types: Array.from(current) });
              }}
            />
          </div>
          <div className="ge-statusbar">
            <span>
              {(view.datasets?.nodes?.preview_rows?.length ?? 0)} nodes ·{" "}
              {(view.datasets?.edges?.preview_rows?.length ?? 0)} edges · hop {state.hops}/2
            </span>
            <span>
              {state.relation_types.length} / {state.catalog?.length ?? 0} profiles
            </span>
          </div>
        </>
      )}
    </div>
  );
}
