import { useEffect, useMemo, useRef, useState } from "react";

import { MaHelpButton } from "../shared/HelpDialog";
import { POOLS_EXPLORER_HELP } from "../shared/helpContent";
import { MaSearchInput } from "../shared/MaSearchInput";
import { MiniAppChrome } from "../shared/MiniAppChrome";
import { TabBar } from "../shared/TabBar";
import { ToastStack } from "../shared/ToastStack";
import { useHydratedDatasets } from "../shared/useHydratedDatasets";
import { useMiniApp } from "../shared/useMiniApp";
import { useSerializedLoader } from "../shared/useSerializedLoader";
import { TokenLabelCoverage } from "./components/TokenLabelCoverage";
import { PoolDetail } from "./detail/PoolDetail";
import { TokenDetail } from "./detail/TokenDetail";
import { devPayload } from "./devFixture";
import { ON_DEMAND_GROUPS } from "./model/datasetGroups";
import { SECTIONS, isListSection } from "./model/navGroups";
import type { HeatmapWindow } from "./model/profileHeatmap";
import {
  collectVisibleTokens, overlayCoverage, overlayScopeKey, type OverlayCoverage,
} from "./model/tokenOverlay";
import { CoverageSection } from "./sections/CoverageSection";
import { OverviewSection } from "./sections/OverviewSection";
import { PoolsSection } from "./sections/PoolsSection";
import { TokensSection } from "./sections/TokensSection";
import type { ApplyOptions, PlxViewContext } from "./sections/common";
import { seedCall } from "./state/navigation";
import {
  EMPTY_DRAFT, buildEntityArgs, buildGroupArgs, buildSearchArgs, buildSectionToolArgs, draftFromState,
  type PlxFilterDraft,
} from "./state/toolArgs";
import { useGroupLoader } from "./state/useGroupLoader";
import { useTokenOverlayLoader } from "./state/useTokenOverlayLoader";
import type { PlxEntityType, PlxListSection, PoolsExplorerViewState } from "./types";
import { clientFromSeed, readUrl, writeUrl, type PlxClientState, type PlxUrlState } from "./urlState";

const APP_ID = "pools_explorer";

/** `make dev` renders the fixture with no server behind it, so every app-only
 * tool call fails by construction (the same reason the fixture pre-marks its
 * groups loaded). The fixture ships the `token_overlay` the tool would have
 * produced; calling it there would only paint a false "chain unreachable". A
 * production build — MCP host or standalone — is never in this branch. */
const DEV_MOCK_MODE = import.meta.env.DEV
  && typeof window !== "undefined"
  && !window.__MINI_APP_API__;

/** Server-paged tables render straight from descriptor pages — excluded from
 * full hydration. */
const LARGE_DATASETS = new Set(["pool_directory", "token_directory", "token_pools"]);

/** Hydration row caps: the heatmap is ≤ 120 × 80 rows, ticks ≤ 10k, the rest small. */
function rowCapFor(key: string): number {
  if (key === "pool_profile_heatmap") return 20_000;
  if (key === "pool_ticks_at") return 10_000;
  return 5_000;
}

/** Frozen warning-code vocabulary → user copy. Unknown strings pass through. */
const WARNING_COPY: Record<string, string> = {
  query_failed: "A dataset failed to load; the others remain available.",
  as_of_shifted: "The requested as-of date has no complete served snapshot; the nearest earlier complete day was used.",
  pool_below_active_threshold: "This pool sat below the active-liquidity threshold: state only, ticks not probed — no profile.",
  reserves_only_pool: "Balancer pool: raw reserves only, no tick liquidity.",
  metadata_unresolved: "Token metadata is unresolved for at least one token — prices and amounts are in raw units.",
  source_stale: "The latest publication is more than two days old.",
  no_indexed_data: "No published data matches this request.",
  token_rpc_unavailable: "Token symbols could not be read from chain state — unnamed tokens are shown as addresses.",
  token_overlay_pending: "More tokens than one chain read can resolve — retry to label the rest.",
};

/** Routine notices surfaced elsewhere (badges, panel states) — never a banner.
 * The two token-overlay codes are quiet by design: a failed LABELLING read is
 * a footnote on the status line, not an error card over the data. */
const QUIET_WARNINGS = new Set(["stale_scope", "token_rpc_unavailable", "token_overlay_pending"]);

/** `metadata_unresolved` is raised by the SERVER, which only knows what the
 * indexer holds — so it still fires after the chain read has labelled every
 * token on screen. Left alone it claims "prices and amounts are in raw units"
 * directly above an adjusted, chain-marked price, which is simply false.
 *
 * The banner is a blanket statement; once the overlay has filled the gap the
 * per-value `chain` marks and the status-line split say the same thing more
 * precisely, so the blanket one is dropped. It survives only while tokens are
 * genuinely still unlabelled, and then it says how many. */
export function metadataWarning(coverage: OverlayCoverage): string | null {
  if (coverage.unlabelled === 0 && coverage.fromChain > 0) return null;
  if (coverage.fromChain > 0) {
    const plural = coverage.unlabelled === 1 ? "token is" : "tokens are";
    return (
      `${coverage.unlabelled} ${plural} still unlabelled and shown as an address ` +
      `with raw amounts; the rest were read from chain state.`
    );
  }
  return WARNING_COPY.metadata_unresolved;
}

function resolveWarnings(
  state: PoolsExplorerViewState,
  coverage: OverlayCoverage,
): string[] {
  return [...new Set([...(state.coverage_warnings ?? []), ...(state.warnings ?? [])])]
    .filter((warning) => !QUIET_WARNINGS.has(warning))
    .map((warning) =>
      warning === "metadata_unresolved"
        ? metadataWarning(coverage)
        : WARNING_COPY[warning] ?? warning,
    )
    .filter((copy): copy is string => copy !== null);
}

export default function PoolsExplorerApp() {
  const { view, callTool, fetchRows, updateModelContext, openLink } = useMiniApp<PoolsExplorerViewState>({
    appId: APP_ID,
    mockPayload: import.meta.env.DEV ? devPayload(window.location.search) : undefined,
  });
  const state = view?.view_state;
  const descriptors = view?.datasets ?? {};

  const aggregateDescriptors = useMemo(
    () => Object.fromEntries(Object.entries(descriptors).filter(([key]) => !LARGE_DATASETS.has(key))),
    [descriptors],
  );
  const hydrated = useHydratedDatasets(
    view?.view_id,
    aggregateDescriptors,
    state?.dataset_revisions,
    fetchRows,
    rowCapFor,
    "geometric",
  );

  const loader = useSerializedLoader<Record<string, unknown>>(
    async (snapshot) => {
      const { __tool, ...args } = snapshot;
      await callTool(String(__tool), args);
    },
    (err) => console.error("[pools_explorer] load failed", err),
    Number(state?.applied_request_id ?? 0),
  );
  const groupLoader = useGroupLoader(callTool);

  const [draft, setDraft] = useState<PlxFilterDraft>(EMPTY_DRAFT);
  const [search, setSearch] = useState("");
  // Bumped when a table appends a page: rows the descriptor preview never
  // carried are now on screen, so the visible token set changed.
  const [pageEpoch, setPageEpoch] = useState(0);
  // One-shot URL deep-link seed (standalone mode): consumed by the FIRST load.
  const urlSeedRef = useRef<PlxUrlState | null | undefined>(undefined);
  if (urlSeedRef.current === undefined) {
    urlSeedRef.current = typeof window !== "undefined" ? readUrl() : null;
  }
  // Client-only pool-detail state lives HERE (not in PoolDetail): the detail
  // view unmounts on every entity switch and would reset its tab.
  const [client, setClientState] = useState<PlxClientState>(() => clientFromSeed(urlSeedRef.current ?? null));
  const previousSection = useRef<PlxListSection>("overview");
  const bootScopeRef = useRef("");

  // Sync draft controls + URL + host model context only when the server
  // applies a new scope/request (local keystrokes must never snap back).
  useEffect(() => {
    if (!state) return;
    if (isListSection(state.section)) previousSection.current = state.section;
    setDraft(draftFromState(state));
    if (typeof window !== "undefined" && window.__MINI_APP_API__) writeUrl(state, client);
    updateModelContext({
      section: state.section,
      as_of: state.as_of || "latest",
      window: state.window,
      filters: state.filters,
      selected_entity: state.selected_entity,
      coverage_warnings: state.coverage_warnings,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.scope_id, state?.applied_request_id]);

  // Client state is URL state too (tab / zoom / axis / view / orientation).
  useEffect(() => {
    if (!state) return;
    if (typeof window !== "undefined" && window.__MINI_APP_API__) writeUrl(state, client);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);

  // Deferred-load driver. open_pools_explorer attaches NO datasets: this effect
  // (a) issues the first load once per scope — the URL seed, the opener's
  // entity, or the section core — then (b) streams every remaining
  // `${section}.${group}` marked unloaded, except the on-demand heatmap, max
  // two at a time, re-running as loaded_groups patches arrive.
  const loadedGroupsKey = JSON.stringify(state?.loaded_groups ?? {});
  useEffect(() => {
    if (!view || !state) return;
    const section = state.section;
    const groups = state.loaded_groups ?? {};
    if (groups[`${section}.core`] === false) {
      if (bootScopeRef.current !== state.scope_id) {
        bootScopeRef.current = state.scope_id;
        const seed = urlSeedRef.current ?? null;
        urlSeedRef.current = null;
        loader.enqueue(seedCall(view.view_id, state, seed));
      }
      return;
    }
    const missing = Object.entries(groups)
      .filter(([key, value]) => key.startsWith(`${section}.`) && value === false && !ON_DEMAND_GROUPS.has(key))
      .map(([key]) => key.slice(section.length + 1));
    if (missing.length > 0) {
      groupLoader.sync(view.view_id, section, missing, state.scope_id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view?.view_id, state?.scope_id, loadedGroupsKey, groupLoader.tick]);

  // TOKEN-OVERLAY HOOK. The state indexer has metadata for 68 of the ~3,400
  // tokens these pools hold, so almost every label would be a bare address.
  // `load_pools_token_metadata` reads the rest straight off the chain over
  // Multicall3 and patches `token_overlay` in. Fired once per SCOPE — the load
  // key is the scope plus a hash of the token set on screen plus the page epoch,
  // so a section switch, an entity change, a deferred group landing or a
  // "Load more" re-runs it and a re-render does not. Deliberately keyed off
  // the visible datasets and NEVER off `token_overlay`: keying on the answer
  // would make each patch trigger the next call.
  const visibleDatasets = useMemo(() => {
    const keys = state?.section ? state.section_datasets?.[state.section] : undefined;
    const names = keys && keys.length > 0 ? keys : Object.keys(descriptors);
    return names
      .map((key) => descriptors[key])
      .filter((descriptor): descriptor is NonNullable<typeof descriptor> => Boolean(descriptor))
      .map((descriptor) => ({
        columns: descriptor.columns.map((column) => column.name),
        rows: descriptor.preview_rows ?? [],
      }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [descriptors, state?.section, state?.section_datasets]);
  const visibleTokens = useMemo(() => collectVisibleTokens(visibleDatasets), [visibleDatasets]);
  const overlayKey = visibleTokens.addresses.length > 0
    ? overlayScopeKey(state?.scope_id ?? "", visibleTokens.addresses, pageEpoch)
    : "";
  const coreLoaded = state ? state.loaded_groups?.[`${state.section}.core`] !== false : false;
  const overlayLoader = useTokenOverlayLoader(
    callTool, view?.view_id ?? "", overlayKey, coreLoaded && !DEV_MOCK_MODE,
  );
  const coverage = useMemo(
    () => overlayCoverage(visibleTokens, state?.token_overlay),
    [visibleTokens, state?.token_overlay],
  );

  if (!view || !state) {
    return <div className="plx-loading">Loading Pool Liquidity Explorer…</div>;
  }

  const viewId = view.view_id;
  const apply = (section: PlxListSection, draftOverride?: PlxFilterDraft, opts: ApplyOptions = {}) => {
    loader.enqueue(buildSectionToolArgs(viewId, section, draftOverride ?? draft, {
      asOf: opts.asOf ?? state.as_of ?? "",
      window: opts.window ?? state.window ?? "",
      forceRefresh: opts.forceRefresh,
    }));
  };
  const loadEntity = (entityType: PlxEntityType, identifier: string, force = false) => {
    if (!identifier) return;
    loader.enqueue(buildEntityArgs(viewId, entityType, identifier, {
      asOf: state.as_of ?? "",
      window: state.window ?? "",
      forceRefresh: force,
    }));
  };
  const submitSearch = () => {
    if (!search.trim()) return;
    loader.enqueue(buildSearchArgs(viewId, search));
  };
  const retryGroup = (section: string, group: string) => {
    groupLoader.retry(section, group, state.scope_id);
    groupLoader.sync(viewId, section, [group], state.scope_id);
  };
  // PROFILE-DATE-HOOK: ONE additive `pool.profile` group load at `date`
  // (the picker debounces 400 ms). Routed through the serialized loader so a
  // burst of picks coalesces to the latest.
  const onLoadProfileDate = (date: string) => {
    loader.enqueue(buildGroupArgs(viewId, "pool", "profile", state.scope_id, { asOf: date }));
  };
  // HEATMAP-HOOK: one additive `pool.heatmap` load for the window. Excluded
  // from background streaming, so this is the only trigger.
  const onLoadHeatmap = (heatmapWindow: HeatmapWindow, opts?: { force?: boolean }) => {
    loader.enqueue(buildGroupArgs(viewId, "pool", "heatmap", state.scope_id, {
      heatmapWindow,
      forceRefresh: opts?.force,
    }));
  };
  const setClient = (patch: Partial<PlxClientState>) => setClientState((current) => ({ ...current, ...patch }));

  const isEntity = state.section === "pool" || state.section === "token";
  const activeSection: PlxListSection = isListSection(state.section) ? state.section : previousSection.current;
  const persistentWarnings = resolveWarnings(state, coverage);

  const ctx: PlxViewContext = {
    state,
    descriptors,
    hydrated,
    viewId,
    fetchRows,
    draft,
    setDraft,
    apply,
    loading: loader.loading,
    onEntity: (entityType, identifier) => loadEntity(entityType, identifier),
    failedGroups: groupLoader.failedGroups(state.scope_id),
    retryGroup,
    openLink: (url) => void openLink(url),
    onLoadProfileDate,
    onLoadHeatmap,
    client,
    setClient,
    overlay: state.token_overlay,
    onPageLoaded: () => setPageEpoch((epoch) => epoch + 1),
  };

  const controls = (
    <div className="plx-controls">
      <button
        type="button"
        disabled={loader.loading}
        title="Bypass caches and reload the current view"
        onClick={() => {
          if (isEntity && state.selected_entity) {
            loadEntity(state.selected_entity.entity_type, state.selected_entity.identifier, true);
          } else {
            apply(activeSection, undefined, { forceRefresh: true });
          }
        }}
      >
        ↻ Refresh
      </button>
      <MaHelpButton content={POOLS_EXPLORER_HELP} />
    </div>
  );

  const subBar = (
    <div className="plx-subbar">
      <TabBar<PlxListSection>
        ariaLabel="Pool explorer sections"
        tabs={SECTIONS.map((section) => ({ id: section.id, label: section.label }))}
        active={activeSection}
        onChange={(section) => apply(section)}
      />
      <MaSearchInput
        ariaLabel="Search pools and tokens"
        value={search}
        onChange={setSearch}
        onSubmit={submitSearch}
        placeholder="Pool or token address, pool name, token symbol"
        actionLabel="Search"
        busy={loader.loading}
        actionDisabled={!search.trim()}
      />
    </div>
  );

  return (
    <MiniAppChrome activeTabId="pools" rightSlot={controls} subBar={subBar} bodyClassName="plx-body">
      <div className="plx-statusline">
        <span>Gnosis Chain DEX pools</span>
        <span>rpc_state_indexer · publication-verified daily state · no USD</span>
        <span>as of {state.as_of || "latest publication"}</span>
        <span>{loader.loading ? "Loading…" : "Ready"}</span>
        <TokenLabelCoverage
          coverage={coverage}
          stats={state.token_overlay_stats}
          warnings={overlayLoader.warnings}
          loading={overlayLoader.loading}
          onRetry={overlayLoader.reload}
        />
      </div>
      {state.search.candidates.length > 0 && (
        <div className="plx-candidates">
          <strong>Choose a match</strong>
          {state.search.candidates.map((candidate) => (
            <button
              key={`${candidate.entity_type}-${candidate.identifier}`}
              type="button"
              onClick={() => loadEntity(candidate.entity_type, candidate.identifier)}
            >
              <span>{candidate.entity_type} · {candidate.role.split("_").join(" ")}</span>
              <code title={candidate.identifier}>{candidate.label || candidate.identifier}</code>
              <span>{candidate.evidence_count.toLocaleString()} {candidate.evidence_count === 1 ? "match" : "matches"}</span>
            </button>
          ))}
        </div>
      )}
      {isEntity && (
        <div className="plx-breadcrumbs">
          <button type="button" onClick={() => apply(previousSection.current)}>
            ← {SECTIONS.find((section) => section.id === previousSection.current)?.label ?? "Back"}
          </button>
          {(state.breadcrumbs ?? []).map((crumb, index, all) => {
            const isCurrent = index === all.length - 1 && state.selected_entity?.identifier === crumb.identifier;
            return (
              <span key={`${crumb.entity_type}-${crumb.identifier}-${index}`} className="plx-crumb-item">
                <span className="plx-crumb-sep">/</span>{" "}
                {isCurrent ? (
                  <span className="is-current">{crumb.label || crumb.identifier}</span>
                ) : (
                  <button type="button" onClick={() => loadEntity(crumb.entity_type, crumb.identifier)}>
                    {crumb.label || crumb.identifier}
                  </button>
                )}
              </span>
            );
          })}
        </div>
      )}
      {persistentWarnings.length > 0 && (
        <div className="plx-warn-strip" role="status">
          {persistentWarnings.map((warning, index) => (
            <span key={`${index}-${warning.slice(0, 24)}`} className="plx-warn-chip">{warning}</span>
          ))}
        </div>
      )}
      <main className="plx-content">
        {state.section === "pool" ? (
          <PoolDetail ctx={ctx} />
        ) : state.section === "token" ? (
          <TokenDetail ctx={ctx} />
        ) : state.section === "pools" ? (
          <PoolsSection ctx={ctx} />
        ) : state.section === "tokens" ? (
          <TokensSection ctx={ctx} />
        ) : state.section === "coverage" ? (
          <CoverageSection ctx={ctx} />
        ) : (
          <OverviewSection ctx={ctx} />
        )}
      </main>
      <ToastStack warnings={loader.error ? [loader.error] : []} autoDismissMs={0} />
    </MiniAppChrome>
  );
}
