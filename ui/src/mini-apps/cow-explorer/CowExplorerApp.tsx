import { useEffect, useMemo, useRef, useState } from "react";
import { MaHelpButton } from "../shared/HelpDialog";
import { COW_EXPLORER_HELP } from "../shared/helpContent";
import { MiniAppChrome } from "../shared/MiniAppChrome";
import { ToastStack } from "../shared/ToastStack";
import { WarningBanner } from "../shared/WarningBanner";
import { useHydratedDatasets, type HydratedDataset } from "../shared/useHydratedDatasets";
import { useMiniApp } from "../shared/useMiniApp";
import { useSerializedLoader } from "../shared/useSerializedLoader";
import { EntityDetail } from "./detail/EntityDetail";
import { MOCK_PAYLOAD } from "./devFixture";
import { SectionViews } from "./sections/SectionViews";
import { buildSectionToolArgs } from "./state/toolArgs";
import { useGroupLoader } from "./state/useGroupLoader";
import {
  FACET_VIEWS,
  isCowFacet,
  type CowNavDestination,
  type FacetHostProps,
} from "./model/navGroups";
import type { CowExplorerViewState, CowFacet, CowSection, EntityType, EnvironmentScope } from "./types";
import { readUrl, writeUrl, type CowUrlState } from "./urlState";
import { CowNav } from "./components/CowNav";
import type { DepthHostProps } from "./components/DepthPanel";
import { InfoPopover } from "./components/InfoPopover";

const APP_ID = "cow_explorer";
const LIVE_GROUPS = ["core", "feed", "intents"];
//: Deferred groups that must NOT be background-streamed — they run an expensive
//: query and only make sense on explicit user action. `markets.depth_heatmap`
//: loads when the Heatmap tab is opened (see DepthPanel's DEPTH-HEATMAP-HOOK).
const ON_DEMAND_GROUPS = new Set(["markets.depth_heatmap"]);
//: Mirror of the server's ALL_NETWORK_SECTIONS — sections that accept chain 0.
//: Facets inherit their HOST section's membership (order_types→orders,
//: solver_directory→solvers, trader_dynamics→traders).
const ALL_NETWORK_SECTIONS = new Set<CowSection>([
  "overview", "trades", "solvers", "traders", "auctions", "orders", "live",
]);
const LARGE_DATASETS = new Set(["recent_market_trades", "trades", "known_orders", "auctions"]);

const WARNING_COPY: Record<string, string> = {
  partial_backfill: "Historical backfill is partial for at least one selected source.",
  stale_source: "The latest source observation is older than the freshness threshold.",
  missing_checkpoint: "No RPC checkpoint is available for at least one network.",
  missing_block_timestamp: "Some indexed records do not have an event timestamp.",
  missing_token_metadata: "Token decimals are missing; normalized prices and depth are suppressed.",
  known_intents_incomplete: "Known open intents are an observed snapshot, not a complete live orderbook.",
  result_truncated: "The result is capped at the newest exact 10,000 rows; narrow filters for the full set.",
  query_failed: "One dataset failed to load; other successful datasets remain available.",
  coarsened_interval: "The requested candle interval was coarsened for this time window.",
  no_indexed_data: "No matching rows were found in the indexed window.",
  all_networks_unsupported: "This section is single-chain; a concrete network was selected for you.",
  depth_reconstructed: "Historical book reconstructed from captured orders, fills, and cancellation events — bounded by the order-capture window.",
  depth_heatmap_reconstructed: "Depth-over-time footprint reconstructed from captured orders, fills, and cancellations; depth is time-weighted per bucket and intra-fill size decay is not modeled.",
  stale_scope: "A background dataset request arrived for a superseded scope and was ignored.",
};

//: Columns whose values are token addresses — the source of the visible-token
//: set the icon overlay resolves. Mirrors the token-identity columns the
//: curated cells render (cells.tsx) so the skip key covers exactly what shows.
const TOKEN_COLUMNS = new Set(["token", "sell_token", "buy_token", "token0", "token1"]);

/** Stable fingerprint of every token address visible in the hydrated
 * datasets (sorted, lowercased, joined). The icon-overlay effect skips its
 * server call when this key is unchanged since the last call — live polls
 * re-attach the same feeds every 30s and must not re-resolve icons each time. */
export function visibleTokenKey(hydrated: Record<string, HydratedDataset>): string {
  const tokens = new Set<string>();
  for (const dataset of Object.values(hydrated)) {
    const indexes = dataset.columns
      .map((column, index) => (TOKEN_COLUMNS.has(column) ? index : -1))
      .filter((index) => index >= 0);
    if (indexes.length === 0) continue;
    for (const row of dataset.rows) {
      for (const index of indexes) {
        const value = row[index];
        if (typeof value === "string" && value.startsWith("0x")) tokens.add(value.toLowerCase());
      }
    }
  }
  return [...tokens].sort().join(",");
}

function resolveWarnings(state: CowExplorerViewState): string[] {
  return [...new Set([...(state.coverage_warnings ?? []), ...(state.warnings ?? [])])]
    .map((warning) => WARNING_COPY[warning] ?? warning);
}

export default function CowExplorerApp() {
  const { view, callTool, fetchRows, updateModelContext, openLink } =
    useMiniApp<CowExplorerViewState>({ appId: APP_ID, mockPayload: MOCK_PAYLOAD });
  const state = view?.view_state;
  const descriptors = view?.datasets ?? {};
  const aggregateDescriptors = useMemo(
    () => Object.fromEntries(Object.entries(descriptors).filter(([key]) => state?.section !== "entity" && !LARGE_DATASETS.has(key))),
    [descriptors, state?.section],
  );
  const hydrated = useHydratedDatasets(
    view?.view_id,
    aggregateDescriptors,
    state?.dataset_revisions,
    fetchRows,
    10_000,
    "geometric",
  );
  const entityGraphDescriptors = useMemo(
    () => state?.section === "entity"
      ? Object.fromEntries(Object.entries(descriptors).filter(([key]) => ["transaction_detail", "transaction_trades", "transaction_interactions", "transaction_competition"].includes(key)))
      : {},
    [descriptors, state?.section],
  );
  const entityHydrated = useHydratedDatasets(
    view?.view_id,
    entityGraphDescriptors,
    state?.dataset_revisions,
    fetchRows,
    500,
    "geometric",
  );
  const loader = useSerializedLoader<Record<string, unknown>>(
    async (snapshot) => {
      const { __tool, ...args } = snapshot;
      await callTool(String(__tool), args);
    },
    (err) => console.error("[cow_explorer] load failed", err),
    Number(state?.applied_request_id ?? 0),
  );
  const short = (value: string) => (value.length > 12 ? `${value.slice(0, 6)}…${value.slice(-4)}` : value);
  // Base/quote pair picker options from the pair_options dataset (symbols +
  // addresses; the user asked for a dropdown instead of raw address inputs).
  const pairOptions = useMemo(() => {
    const ds = hydrated.pair_options;
    if (!ds) return [] as Array<{ value: string; label: string; title: string }>;
    const idx = new Map(ds.columns.map((column, index) => [column, index]));
    const cell = (row: unknown[], name: string) => String(row[idx.get(name) ?? -1] ?? "");
    return ds.rows
      .map((row) => {
        const t0 = cell(row, "token0");
        const t1 = cell(row, "token1");
        if (!t0 || !t1) return null;
        const s0 = cell(row, "token0_symbol") || short(t0);
        const s1 = cell(row, "token1_symbol") || short(t1);
        const fills = Number(row[idx.get("fill_count") ?? -1] ?? 0);
        return {
          value: `${t0}|${t1}`,
          label: `${s0}/${s1} · ${fills.toLocaleString()} fills`,
          title: `${t0} / ${t1}`,
        };
      })
      .filter((option): option is { value: string; label: string; title: string } => option !== null);
  }, [hydrated.pair_options]);
  const [search, setSearch] = useState("");
  const [base, setBase] = useState("");
  const [quote, setQuote] = useState("");
  const [owner, setOwner] = useState("");
  const [token, setToken] = useState("");
  const [status, setStatus] = useState("");
  const [solver, setSolver] = useState("");
  const [startAt, setStartAt] = useState("");
  const [endAt, setEndAt] = useState("");
  const [windowDays, setWindowDays] = useState("30");
  // Active frontend facet (CLIENT-only view over a server section; the server
  // never sees it). Seeded from the ?facet= URL param on first load.
  const [facet, setFacet] = useState<CowFacet | null>(() =>
    typeof window !== "undefined" ? (readUrl().facet || null) : null,
  );
  const previousSection = useRef<Exclude<CowSection, "entity">>("overview");
  const groupLoader = useGroupLoader(callTool);
  const bootScopeRef = useRef("");
  const iconRetriedRef = useRef<Set<string>>(new Set());
  // Icon-overlay economy: the `${view_id}|${visibleTokenKey}` of the LAST
  // overlay call — an unchanged key (live polls re-attaching the same feeds)
  // skips the call entirely. Read at fire time via hydratedRef so the key
  // reflects whatever pages have landed by then.
  const iconTokenKeyRef = useRef<string | null>(null);
  const hydratedRef = useRef(hydrated);
  hydratedRef.current = hydrated;
  // One-shot URL deep-link seed (standalone mode): filters/window/entity that
  // the server open route cannot map are applied by the FIRST section apply.
  const urlSeedRef = useRef<CowUrlState | null | undefined>(undefined);
  if (urlSeedRef.current === undefined) {
    urlSeedRef.current = typeof window !== "undefined" ? readUrl() : null;
  }

  useEffect(() => {
    if (!state) return;
    if (state.section !== "entity") previousSection.current = state.section;
    setBase(state.pair.base);
    setQuote(state.pair.quote);
    setOwner(state.filters.owner);
    setToken(state.filters.token);
    setStatus(state.filters.status);
    setSolver(state.filters.solver);
    setStartAt(state.date_range.kind === "absolute" ? state.date_range.start_at.replace("Z", "").slice(0, 16) : "");
    setEndAt(state.date_range.kind === "absolute" ? state.date_range.end_at.replace("Z", "").slice(0, 16) : "");
    if (state.date_range.kind === "relative" && state.date_range.window_days) setWindowDays(String(state.date_range.window_days));
    updateModelContext({
      section: state.section,
      scope: state.environment_scope,
      chain: state.chain_name,
      pair: state.pair,
      indexed_window: state.date_range,
      selected_entity: state.selected_entity,
      coverage_warnings: state.coverage_warnings,
    });
  // Sync draft controls only when the server applies a new scope/request.
  // Depending on the full state object made controlled inputs snap back on
  // every local keystroke because some hosts recreate payload objects.
  }, [state?.scope_id, state?.applied_request_id]);

  // URL sync lives apart from the draft-sync effect above because the facet
  // is CLIENT state: toggling it must rewrite the URL without snapping the
  // draft filter inputs back to server state.
  useEffect(() => {
    if (!state) return;
    if (typeof window !== "undefined" && window.__MINI_APP_API__) writeUrl(state, facet);
  }, [state?.scope_id, state?.applied_request_id, facet]);

  // Deferred-load driver. open_cow_explorer attaches NO datasets; this effect
  // (a) applies the initial section once (loads its core group), then
  // (b) streams every remaining `${section}.${group}` marked unloaded, max
  // two at a time, re-running as loaded_groups patches arrive.
  const loadedGroupsKey = JSON.stringify(state?.loaded_groups ?? {});
  useEffect(() => {
    if (!view || !state) return;
    const section = state.section === "entity" ? null : state.section;
    if (!section) return;
    const groups = state.loaded_groups ?? {};
    if (groups[`${section}.core`] === false) {
      if (bootScopeRef.current !== state.scope_id) {
        bootScopeRef.current = state.scope_id;
        const seed = urlSeedRef.current;
        // Entity deep link (?entity=…&id=…): resolve it instead of the section.
        if (seed?.entity && seed.id) {
          urlSeedRef.current = null;
          loader.enqueue({
            __tool: "load_cow_entity",
            view_id: view.view_id,
            entity_type: seed.entity,
            identifier: seed.id,
            chain_id: seed.chain || state.chain_id,
          });
          return;
        }
        const overrides: Record<string, unknown> = {};
        if (seed) {
          if (seed.days !== -1) overrides.window_days = seed.days;
          if (seed.owner) overrides.owner = seed.owner;
          if (seed.token) overrides.token = seed.token;
          if (seed.status) overrides.status = seed.status;
          if (seed.solver) overrides.solver = seed.solver;
          if (seed.start && seed.end) {
            overrides.start_at = seed.start;
            overrides.end_at = seed.end;
            overrides.window_days = -1;
          }
          urlSeedRef.current = null;
        }
        // A ?facet= deep link implies its HOST section (the facet is
        // client-only; the server open route cannot map it). `facet` state
        // was already seeded from the same URL read.
        const bootSection = seed?.facet && FACET_VIEWS[seed.facet].section !== section
          ? FACET_VIEWS[seed.facet].section
          : section;
        loader.enqueue(buildSectionToolArgs(
          view.view_id, state, bootSection,
          { base, quote, status, owner, token, solver },
          overrides,
        ));
      }
      return;
    }
    const missing = Object.entries(groups)
      .filter(([key, value]) => key.startsWith(`${section}.`) && value === false && !ON_DEMAND_GROUPS.has(key))
      .map(([key]) => key.slice(section.length + 1));
    if (missing.length > 0) {
      groupLoader.sync(view.view_id, section, missing, state.scope_id);
    }
  }, [view?.view_id, state?.scope_id, loadedGroupsKey, groupLoader.tick]);

  // Facet group sync (mirrors the LIVE_GROUPS sync): once a facet's HOST
  // section is applied, eagerly enqueue the facet's dataset groups so its
  // view hydrates ahead of the generic background streaming above. Guarded
  // until view_id/scope_id exist and the host core apply has landed; groups
  // the server does not expose yet simply never flip to `false` and are
  // skipped (safe while the backend groups land separately).
  useEffect(() => {
    if (!view || !state || !facet) return;
    const { section: hostSection, groups: facetGroups } = FACET_VIEWS[facet];
    if (state.section !== hostSection || !state.scope_id) return;
    const groups = state.loaded_groups ?? {};
    if (groups[`${hostSection}.core`] === false) return; // host apply pending
    const missing = facetGroups.filter((group) => groups[`${hostSection}.${group}`] === false);
    if (missing.length > 0) {
      groupLoader.sync(view.view_id, hostSection, missing, state.scope_id);
    }
  }, [view?.view_id, facet, state?.scope_id, loadedGroupsKey, groupLoader.tick]);

  // Async token-icon overlay: after datasets settle, ask the server to map
  // visible tokens to CoinGecko icons (never blocks data loads). One retry
  // per revision snapshot when the server reports the fetch is still pending.
  const revisionsKey = JSON.stringify(state?.dataset_revisions ?? {});
  useEffect(() => {
    if (!view || !state) return;
    if (Object.keys(state.dataset_revisions ?? {}).length === 0) return;
    const viewIdNow = view.view_id;
    const timer = setTimeout(() => {
      // Skip when the visible token set is unchanged since the last call —
      // the overlay for those tokens is already resolved (or pending its one
      // retry below). An empty key means hydration has not surfaced any
      // token column yet; stay conservative and let the server decide.
      const tokenKey = visibleTokenKey(hydratedRef.current);
      const callKey = `${viewIdNow}|${tokenKey}`;
      if (tokenKey && callKey === iconTokenKeyRef.current) return;
      iconTokenKeyRef.current = callKey;
      void callTool("load_cow_icon_overlay", { view_id: viewIdNow }).then((result) => {
        const payload = result as { warnings?: string[] } | null;
        if (
          payload?.warnings?.includes("icon_overlay_pending")
          && !iconRetriedRef.current.has(revisionsKey)
        ) {
          iconRetriedRef.current.add(revisionsKey);
          setTimeout(() => {
            void callTool("load_cow_icon_overlay", { view_id: viewIdNow });
          }, 5000);
        }
      });
    }, 800);
    return () => clearTimeout(timer);
  }, [view?.view_id, revisionsKey]);

  if (!view || !state) {
    return <div className="cow-loading">Loading CoW Data Explorer…</div>;
  }

  const viewId = view.view_id;
  // Raw section apply — never touches facet state (navigate() manages it).
  const applySection = (
    section: Exclude<CowSection, "entity">,
    overrides: Record<string, unknown> = {},
  ) => {
    loader.enqueue(buildSectionToolArgs(
      viewId, state, section, { base, quote, status, owner, token, solver }, overrides,
    ));
  };
  const sendSection = (
    section: Exclude<CowSection, "entity">,
    overrides: Record<string, unknown> = {},
  ) => {
    // Leaving a facet's host section (pair click-through, chain quick-links,
    // entity back into another section, …) deactivates the facet; re-applying
    // the host (filters, refresh, network change) keeps it.
    if (facet && FACET_VIEWS[facet].section !== section) setFacet(null);
    applySection(section, overrides);
  };
  const navigate = (dest: CowNavDestination) => {
    if (isCowFacet(dest)) {
      const hostSection = FACET_VIEWS[dest].section;
      setFacet(dest);
      // Same host already applied → the facet sync effect loads its groups;
      // otherwise apply the host section first (groups follow once it lands).
      if (state.section !== hostSection) applySection(hostSection);
      return;
    }
    setFacet(null);
    applySection(dest);
  };
  const loadEntity = (entityType: EntityType, identifier: string, chainId = state.chain_id) => {
    if (!identifier || !chainId) return;
    loader.enqueue({ __tool: "load_cow_entity", view_id: viewId, entity_type: entityType, identifier, chain_id: chainId });
  };
  const submitSearch = () => {
    if (!search.trim()) return;
    loader.enqueue({ __tool: "search_cow_explorer", view_id: viewId, query: search.trim(), chain_id: state.chain_id });
  };
  const persistentWarnings = resolveWarnings(state);
  const activeSection = state.section === "entity" ? previousSection.current : state.section;
  // Nav highlight: the facet wins over the raw server section (entity view
  // keeps the previousSection fallback via activeSection above).
  const activeDestination: CowNavDestination = facet ?? activeSection;
  // The active destination's HOST server section — drives capability checks
  // (all-networks support) even while the host apply is still in flight.
  const hostSection = facet ? FACET_VIEWS[facet].section : activeSection;
  // FACET-HOOK: threaded into <SectionViews> below via typed object spread.
  // `facet` is non-null ONLY when its host section is the currently rendered
  // server section, so the sections agent can dispatch on it directly:
  //   props.facet === "order_types"      → render OrderTypesSection
  //   props.facet === "solver_directory" → render SolverDirectorySection
  //   props.facet === "trader_dynamics"  → render TraderDynamicsSection
  // SectionViews' Props (sections/SectionViews.tsx) does not declare `facet`
  // yet — extend it with FacetHostProps from model/navGroups.ts. Until then
  // the prop is ignored and the host section's normal view renders.
  const facetHostProps: FacetHostProps = {
    facet: facet && FACET_VIEWS[facet].section === state.section ? facet : null,
  };
  // DEPTH-HOOK: threaded into <SectionViews> below via typed object spread,
  // same channel as FACET-HOOK. The Markets depth panel
  // (components/DepthPanel.tsx) calls this to move the pair book in time:
  // "live" clears back to the live book, an ISO-8601 timestamp reconstructs
  // the book at that moment. It is ONE additive `markets.depth` group load —
  // the applied value is patched into state.depth_at server-side, and every
  // section apply resets it to "". Routed through the serialized loader (the
  // same mechanism section applies and entity loads use) so a burst of slider
  // moves coalesces to the latest snapshot; the loader stamps request_id
  // (group loads ignore it server-side). SectionViews' Props does not declare
  // `onLoadDepthAt` yet — the sections agent extends it with DepthHostProps
  // and forwards the prop to <DepthPanel {...props} />.
  const depthHostProps: DepthHostProps = {
    onLoadDepthAt: (ts) => {
      loader.enqueue({
        __tool: "load_cow_explorer_datasets",
        view_id: viewId,
        section: "markets",
        group: "depth",
        scope_id: state.scope_id,
        depth_at: ts,
      });
    },
    // DEPTH-HEATMAP-HOOK: one additive markets.depth_heatmap group load for the
    // chosen window. Excluded from the background auto-sync below, so it only
    // runs when the user opens the Heatmap tab (or changes its window).
    onLoadDepthHeatmap: (heatmapWindow, opts) => {
      loader.enqueue({
        __tool: "load_cow_explorer_datasets",
        view_id: viewId,
        section: "markets",
        group: "depth_heatmap",
        scope_id: state.scope_id,
        heatmap_window: heatmapWindow,
        // 0 = auto (server picks span/60); -1 would mean "keep current".
        bucket_seconds: opts?.bucketSeconds ?? 0,
        // Retry-after-failure: bypass the server's negative failure cache.
        ...(opts?.force ? { force_refresh: true } : {}),
      });
    },
  };
  const utcValue = (value: string) => value ? `${value.length === 16 ? `${value}:00` : value}Z` : "";

  const controls = (
    <div className="cow-controls">
      <select
        aria-label="Environment"
        value={state.environment_scope}
        onChange={(event) => {
          const scope = event.target.value as EnvironmentScope;
          setFacet(null); // scope switch resets to overview — no facet host
          loader.enqueue({
            __tool: "load_cow_explorer_section", view_id: viewId, section: "overview",
            environment_scope: scope, chain_id: 0, window_days: 30,
          });
        }}
      >
        <option value="production">Production</option><option value="testnet">Testnet</option>
      </select>
      <select
        aria-label="Network"
        value={state.chain_id}
        onChange={(event) => sendSection(state.section === "entity" ? previousSection.current : state.section, { chain_id: Number(event.target.value), base_token: "", quote_token: "" })}
      >
        {ALL_NETWORK_SECTIONS.has(hostSection) && <option value={0}>All networks</option>}
        {state.chain_options.map((chain) => <option key={chain.chain_id} value={chain.chain_id}>{chain.name}</option>)}
      </select>
      <button type="button" disabled={loader.loading} onClick={() => sendSection(state.section === "entity" ? previousSection.current : state.section, { force_refresh: true })}>↻ Refresh</button>
      <MaHelpButton content={COW_EXPLORER_HELP} />
    </div>
  );

  const subBar = (
    <CowNav
      activeDestination={activeDestination}
      onNavigate={navigate}
      searchSlot={(
        <form className="cow-search" onSubmit={(event) => { event.preventDefault(); submitSearch(); }}>
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Order UID, tx, address, auction ID, symbol" aria-label="Universal CoW search" />
          <button type="submit" disabled={loader.loading || !search.trim()}>Search</button>
        </form>
      )}
    />
  );

  return (
    <MiniAppChrome activeTabId="cow" rightSlot={controls} subBar={subBar} bodyClassName="cow-body">
      <div className="cow-controls-block">
      <div className="cow-statusline">
        <span>{state.chain_name}</span><span>{state.environment_scope}</span>
        <span>{state.date_range.kind === "all" ? "All indexed history" : `${state.date_range.window_days ?? "Custom"} day indexed window`}</span>
        <span>{loader.loading ? "Loading…" : "Ready"}</span>
        {persistentWarnings.length > 0 && (
          <InfoPopover label={`${persistentWarnings.length} data note${persistentWarnings.length === 1 ? "" : "s"}`}>
            <ul>{persistentWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
          </InfoPopover>
        )}
      </div>
      {state.search.candidates.length > 0 && (
        <div className="cow-candidates">
          <strong>Choose a matching role or network</strong>
          {state.search.candidates.map((candidate) => (
            <button key={`${candidate.chain_id}-${candidate.role}-${candidate.identifier}`} type="button" onClick={() => loadEntity(candidate.entity_type, candidate.identifier, candidate.chain_id)}>
              <span>{candidate.chain_name}</span><span>{candidate.role.split("_").join(" ")}</span><code>{candidate.identifier}</code><span>{candidate.evidence_count.toLocaleString()} matches</span>
            </button>
          ))}
        </div>
      )}
      {activeSection !== "live" && (
        <div className="cow-timebar">
          <label>Trailing indexed days
            <input type="number" min={1} step={1} value={windowDays} onChange={(event) => setWindowDays(event.target.value)} />
          </label>
          <button type="button" disabled={!Number.isInteger(Number(windowDays)) || Number(windowDays) < 1} onClick={() => sendSection(activeSection, { window_days: Number(windowDays), start_at: "", end_at: "" })}>Apply</button>
          <button type="button" onClick={() => sendSection(activeSection, { window_days: 0, start_at: "", end_at: "" })}>All indexed</button>
          <details className="cow-range-popover">
            <summary>Exact UTC range</summary>
            <div className="cow-range-popover__panel">
              <label>Start UTC<input type="datetime-local" value={startAt} onChange={(event) => setStartAt(event.target.value)} /></label>
              <label>End UTC<input type="datetime-local" value={endAt} onChange={(event) => setEndAt(event.target.value)} /></label>
              <button type="button" disabled={!startAt || !endAt} onClick={() => sendSection(activeSection, { start_at: utcValue(startAt), end_at: utcValue(endAt), window_days: -1 })}>Apply exact range</button>
            </div>
          </details>
        </div>
      )}
      {(activeSection === "markets" || activeSection === "orders" || activeSection === "solvers") && (
        <div className="cow-filterbar">
          <label>Pair
            <select
              value={`${state.pair.base}|${state.pair.quote}`}
              onChange={(event) => {
                const [pairBase, pairQuote] = event.target.value.split("|");
                if (pairBase && pairQuote) sendSection(activeSection, { base_token: pairBase, quote_token: pairQuote });
              }}
            >
              {pairOptions.every((option) => option.value !== `${state.pair.base}|${state.pair.quote}`) && state.pair.base && (
                <option value={`${state.pair.base}|${state.pair.quote}`}>
                  {(state.pair.base_symbol || short(state.pair.base))}/{(state.pair.quote_symbol || short(state.pair.quote))} (current)
                </option>
              )}
              {pairOptions.map((option) => <option key={option.value} value={option.value} title={option.title}>{option.label}</option>)}
            </select>
          </label>
          <button type="button" onClick={() => sendSection(activeSection, { base_token: state.pair.quote, quote_token: state.pair.base })}>⇄ Invert</button>
          <details className="cow-range-popover">
            <summary>Custom pair</summary>
            <div className="cow-range-popover__panel">
              <label>Base address<input value={base} onChange={(event) => setBase(event.target.value)} placeholder="0x…" /></label>
              <label>Quote address<input value={quote} onChange={(event) => setQuote(event.target.value)} placeholder="0x…" /></label>
              <button type="button" disabled={!base || !quote} onClick={() => sendSection(activeSection, { base_token: base, quote_token: quote })}>Apply custom pair</button>
            </div>
          </details>
          {activeSection !== "solvers" && <label>Interval<select value={state.interval} onChange={(event) => sendSection(activeSection, { interval: event.target.value })}><option value="5m">5m</option><option value="15m">15m</option><option value="30m">30m</option><option value="1h">1h</option><option value="2h">2h</option><option value="4h">4h</option><option value="12h">12h</option><option value="1d">1d</option><option value="1w">1w</option></select></label>}
          {activeSection === "orders" && <label>Status<select value={status} onChange={(event) => { setStatus(event.target.value); sendSection("orders", { status: event.target.value }); }}><option value="">All</option><option value="open">Open</option><option value="fulfilled">Fulfilled</option><option value="cancelled">Cancelled</option><option value="expired">Expired</option></select></label>}
          {activeSection === "orders" && <label>Owner<input value={owner} onChange={(event) => setOwner(event.target.value)} placeholder="0x…" /></label>}
          {activeSection === "solvers" && <label>Role address<input value={solver} onChange={(event) => setSolver(event.target.value)} placeholder="Competition solver / executor" /></label>}
          <button type="button" onClick={() => sendSection(activeSection, { base_token: base, quote_token: quote, status, solver })}>Apply</button>
        </div>
      )}
      {activeSection === "trades" && (
        <div className="cow-filterbar">
          <label>Owner<input value={owner} onChange={(event) => setOwner(event.target.value)} onBlur={() => sendSection("trades", { owner, token })} onKeyDown={(event) => { if (event.key === "Enter") sendSection("trades", { owner, token }); }} placeholder="0x…" /></label>
          <label>Token<input value={token} onChange={(event) => setToken(event.target.value)} onBlur={() => sendSection("trades", { owner, token })} onKeyDown={(event) => { if (event.key === "Enter") sendSection("trades", { owner, token }); }} placeholder="0x…" /></label>
        </div>
      )}
      </div>
      <WarningBanner
        warnings={(state.warnings ?? []).filter((w) => w.includes(" unavailable: "))}
      />
      <main className="cow-content">
        {state.section === "entity" ? (
          <EntityDetail state={state} descriptors={descriptors} hydrated={entityHydrated} viewId={viewId} fetchRows={fetchRows} onBack={() => sendSection(previousSection.current)} onEntity={loadEntity} openExternal={(url) => void openLink(url)} />
        ) : (
          <SectionViews
            {...facetHostProps} /* FACET-HOOK: see facetHostProps above */
            {...depthHostProps} /* DEPTH-HOOK: see depthHostProps above */
            state={state}
            descriptors={descriptors}
            hydrated={hydrated}
            viewId={viewId}
            fetchRows={fetchRows}
            onEntity={loadEntity}
            onSelectChain={(chainId) => sendSection(activeSection, { chain_id: chainId, base_token: "", quote_token: "" })}
            onSelectPair={(pairBase, pairQuote, chainId) => sendSection("markets", {
              base_token: pairBase,
              quote_token: pairQuote,
              chain_id: chainId ?? (state.chain_id || undefined),
            })}
            failedGroups={groupLoader.failedGroups(state.scope_id)}
            onRetryGroup={(section, group) => groupLoader.retry(section, group, state.scope_id)}
            onRefreshLive={() => {
              // Clear failure markers each poll cycle so a transient failure
              // never permanently freezes a live feed.
              groupLoader.retryAll(state.scope_id);
              groupLoader.sync(viewId, "live", LIVE_GROUPS, state.scope_id);
            }}
            liveAutoDefault={Boolean(
              // Auto-poll only where tool calls can actually reach the server
              // (standalone web mode). Embedded hosts default to paused
              // (existing behavior), and so does the pure-vite mock fixture —
              // its polls could only fail and flip live groups to error cards.
              typeof window !== "undefined" && window.__MINI_APP_API__,
            )}
          />
        )}
      </main>
      <ToastStack warnings={loader.error ? [loader.error] : []} autoDismissMs={0} />
    </MiniAppChrome>
  );
}
