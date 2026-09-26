import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MiniAppChrome } from "../shared/MiniAppChrome";
import { TabBar } from "../shared/TabBar";
import { ToastStack } from "../shared/ToastStack";
import { MaSearchInput } from "../shared/MaSearchInput";
import { useGroupLoader } from "../shared/useGroupLoader";
import { useHydratedDatasets } from "../shared/useHydratedDatasets";
import { useMiniApp } from "../shared/useMiniApp";
import { useSerializedLoader } from "../shared/useSerializedLoader";
import { FreshnessStrip } from "./components/FreshnessStrip";
import { ContributorDetail } from "./detail/ContributorDetail";
import { ProposalDetail } from "./detail/ProposalDetail";
import { TopicDetail } from "./detail/TopicDetail";
import { TreasuryTokenDetail } from "./detail/TreasuryTokenDetail";
import { TreasuryWalletDetail } from "./detail/TreasuryWalletDetail";
import { VoterDetail } from "./detail/VoterDetail";
import { devPayload } from "./devFixture";
import { buildModelContextLines, type GovAggregates } from "./model/contextPrompt";
import { parseSpaceSummary } from "./model/parseRows";
import { PROVENANCE_LINE } from "./model/treasuryCopy";
import { DelegationsSection } from "./sections/DelegationsSection";
import { TreasurySection } from "./sections/TreasurySection";
import { ForumSection } from "./sections/ForumSection";
import { GraphSection } from "./sections/GraphSection";
import { OverviewSection } from "./sections/OverviewSection";
import { ProposalsSection } from "./sections/ProposalsSection";
import { VotersSection } from "./sections/VotersSection";
import type { GovViewContext } from "./sections/common";
import {
  crumbCall,
  entityCall,
  returnSectionFor,
  sectionReturnCall,
  seedCall,
  trailForDisplay,
  type GovSectionId,
} from "./state/navigation";
import { isTreasuryContext, overlayRequestKey, shouldRequestOverlay } from "./state/overlay";
import { resolveWarnings } from "./state/warnings";
import { buildSearchArgs, buildSectionToolArgs, EMPTY_DRAFT, type GovFilterDraft } from "./state/toolArgs";
import {
  applyTreasuryPatch,
  initialTreasuryView,
  type TreasuryViewState,
} from "./state/treasuryView";
import type { GovEntityType, GovernanceViewState } from "./types";
import { readUrl, writeUrl, type GovUrlState } from "./urlState";

const APP_ID = "governance";

const SECTIONS: Array<{ id: GovSectionId; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "proposals", label: "Proposals" },
  { id: "voters", label: "Voters" },
  { id: "forum", label: "Forum" },
  { id: "delegations", label: "Delegations" },
  { id: "treasury", label: "Treasury" },
  { id: "graph", label: "Graph" },
];

/** Large paginated datasets render via PaginatedTable / local paging — they
 * are excluded from full hydration (CSV export re-pages them on demand). */
const LARGE_DATASETS = new Set([
  "proposals",
  "proposal_votes",
  "voter_votes",
  "forum_topics",
  "voter_leaderboard",
  "topic_posts",
  "contributor_posts",
  "contributor_leaderboard",
  "top_delegates",
  // Table-only poll/like datasets: PaginatedTable renders them straight from
  // descriptor pages; full hydration would buy nothing.
  "forum_polls",
  "most_liked_topics",
  // The treasury datasets are deliberately NOT here: every treasury filter is
  // client-side (chain, Gnosis Ltd., hidden tokens) and the charts need the
  // FULL history (~5k rows against the 10k hydration cap), so they hydrate
  // completely and the charts wait for `phase === "complete"`.
]);

function draftFromState(state: GovernanceViewState): GovFilterDraft {
  const range = state.date_range;
  return {
    days: range.kind === "absolute"
      ? null
      : range.kind === "relative"
        ? (range.window_days === 90 || range.window_days === 365 ? range.window_days : 0)
        : 0,
    start: range.kind === "absolute" ? range.start_at : "",
    end: range.kind === "absolute" ? range.end_at : "",
    query: state.filters.query,
    proposal_state: state.filters.proposal_state,
    proposal_type: state.filters.proposal_type,
    quorum_status: state.filters.quorum_status,
    category_id: state.filters.category_id,
    forum_status: state.filters.forum_status,
    sort_by: state.filters.sort_by,
  };
}

export default function GovernanceApp() {
  const { view, callTool, fetchRows, updateModelContext, sendMessage, openLink } =
    useMiniApp<GovernanceViewState>({
      appId: APP_ID,
      mockPayload: import.meta.env.DEV ? devPayload(window.location.search) : undefined,
    });
  const state = view?.view_state;
  const descriptors = view?.datasets ?? {};

  // Hydrate chart/aggregate datasets fully; large paginated tables are
  // server-paged and stay on descriptor previews.
  const aggregateDescriptors = useMemo(
    () => Object.fromEntries(
      Object.entries(descriptors).filter(([key]) => !LARGE_DATASETS.has(key)),
    ),
    [descriptors],
  );
  const hydrated = useHydratedDatasets(
    view?.view_id,
    aggregateDescriptors,
    state?.dataset_revisions,
    fetchRows,
    10_000,
    "geometric",
  );

  const loader = useSerializedLoader<Record<string, unknown>>(
    async (snapshot) => {
      const { __tool, ...args } = snapshot;
      await callTool(String(__tool), args);
    },
    (err) => console.error("[governance] load failed", err),
    Number(state?.applied_request_id ?? 0),
  );

  const groupLoader = useGroupLoader(callTool, "load_governance_datasets");
  const [draft, setDraft] = useState<GovFilterDraft>(EMPTY_DRAFT);
  const [search, setSearch] = useState("");
  const previousSection = useRef<GovSectionId>("overview");
  const bootScopeRef = useRef("");
  // De-dup key for the overlay call + the one-shot retry ledger, so a re-render
  // never re-hits the rate-limited CoinGecko path for an unchanged token set.
  const overlayKeyRef = useRef("");
  const overlayRetriedRef = useRef(new Set<string>());
  // One-shot URL deep-link seed (standalone mode): consumed by the FIRST
  // section apply; `entity`+`id` params short-circuit to the entity load.
  const urlSeedRef = useRef<GovUrlState | null | undefined>(undefined);
  if (urlSeedRef.current === undefined) {
    urlSeedRef.current = typeof window !== "undefined" ? readUrl() : null;
  }
  // The treasury keys the URL carried at boot (the seed above is consumed by
  // the first apply; these must outlive it for the hint merge below).
  const urlTreasuryRef = useRef<Partial<TreasuryViewState>>(urlSeedRef.current?.treasury ?? {});
  // Client-side treasury view (tab, chain, Gnosis Ltd., hidden tokens, history
  // controls). Lives HERE, not in TreasurySection: the section unmounts on an
  // entity drill-down, and the view must survive the round trip.
  const [treasuryView, setTreasuryView] = useState<TreasuryViewState>(
    () => initialTreasuryView(urlTreasuryRef.current),
  );
  const updateTreasuryView = useCallback((patch: Partial<TreasuryViewState>) => {
    setTreasuryView((prev) => applyTreasuryPatch(prev, patch));
  }, []);
  // "Now" for snapshot staleness, fixed per mount so renders agree.
  const [now] = useState(() => Date.now());
  const hintsAppliedRef = useRef(false);

  const aggregates = useMemo<GovAggregates>(() => {
    const empty: GovAggregates = {};
    const descriptor = descriptors.space_summary;
    if (!descriptor) return empty;
    const summary = parseSpaceSummary({
      columns: descriptor.columns.map((column) => column.name),
      rows: descriptor.preview_rows,
    });
    if (!summary) return empty;
    return {
      proposals: summary.proposal_count,
      votes: summary.vote_count,
      "unique voters": summary.voter_count,
      followers: summary.follower_count,
      "forum topics": summary.topic_count,
      "forum posts": summary.post_count,
    };
  }, [descriptors.space_summary]);

  // Sync draft controls + URL + host model context only when the server
  // applies a new scope/request (local keystrokes must never snap back).
  useEffect(() => {
    if (!state) return;
    if (state.section !== "entity") previousSection.current = state.section;
    setDraft(draftFromState(state));
    if (typeof window !== "undefined" && window.__MINI_APP_API__) writeUrl(state, treasuryView);
    updateModelContext(buildModelContextLines(state, aggregates, treasuryView));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.scope_id, state?.applied_request_id]);

  // The server's treasury filters are INITIAL-VIEW HINTS (an assistant opening
  // the treasury on one chain): merged once, and only where the URL is silent.
  useEffect(() => {
    if (!state || hintsAppliedRef.current) return;
    hintsAppliedRef.current = true;
    const hinted = initialTreasuryView(urlTreasuryRef.current, state.filters);
    setTreasuryView((prev) => (
      prev.chain === hinted.chain && prev.exLtd === hinted.exLtd
        ? prev
        : { ...prev, chain: hinted.chain, exLtd: hinted.exLtd }
    ));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.scope_id]);

  // A treasury view change is client-side: no tool call, just the URL
  // (standalone, replaceState) and the host's model context.
  const treasuryViewKey = JSON.stringify(treasuryView);
  useEffect(() => {
    if (!state || !isTreasuryContext(state)) return;
    if (typeof window !== "undefined" && window.__MINI_APP_API__) writeUrl(state, treasuryView);
    updateModelContext(buildModelContextLines(state, aggregates, treasuryView));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [treasuryViewKey]);

  // Deferred-load driver: open_governance attaches NO datasets. This effect
  // (a) applies the initial section once — consuming the one-shot URL seed —
  // then (b) streams every remaining `${section}.${group}` marked unloaded,
  // max two at a time, re-running as loaded_groups patches arrive.
  const loadedGroupsKey = JSON.stringify(state?.loaded_groups ?? {});
  useEffect(() => {
    if (!view || !state) return;
    const section = state.section === "entity" ? null : state.section;
    if (!section) return;
    const groups = state.loaded_groups ?? {};
    if (groups[`${section}.core`] === false) {
      if (bootScopeRef.current !== state.scope_id) {
        bootScopeRef.current = state.scope_id;
        const seed = urlSeedRef.current ?? null;
        urlSeedRef.current = null;
        loader.enqueue(seedCall(view.view_id, seed, section, draft));
      }
      return;
    }
    const missing = Object.entries(groups)
      .filter(([key, value]) => key.startsWith(`${section}.`) && value === false)
      .map(([key]) => key.slice(section.length + 1));
    if (missing.length > 0) {
      groupLoader.sync(view.view_id, section, missing, state.scope_id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view?.view_id, state?.scope_id, loadedGroupsKey, groupLoader.tick]);

  // CoinGecko icons + the spot FALLBACK quotes, for the treasury section AND
  // its wallet / token pages (a cold link to a wallet page used to get no
  // icons and no spot subtotal). Runs AFTER the datasets settle because it
  // resolves the tokens actually loaded, and it never blocks a data load: the
  // server returns whatever is cached and reports `overlay_pending` when a
  // background fetch will find more. Pricing needs two hops (contract -> coin
  // id -> quote), so exactly one retry is scheduled.
  const overlayKey = view && state ? overlayRequestKey(view.view_id, state) : "";
  useEffect(() => {
    if (!view || !state) return;
    if (!shouldRequestOverlay(state)) return;
    const viewId = view.view_id;
    const key = overlayKey;
    const timer = setTimeout(() => {
      if (overlayKeyRef.current === key) return;
      overlayKeyRef.current = key;
      // The overlay is an enhancement: a failed request leaves icons and the
      // spot subtotal "pending", never an unhandled rejection.
      const warn = (err: unknown) => console.warn("[governance] overlay request failed", err);
      void callTool("load_governance_overlays", { view_id: viewId }).then((result) => {
        const payload = result as { warnings?: string[] } | null;
        if (payload?.warnings?.includes("overlay_pending") && !overlayRetriedRef.current.has(key)) {
          overlayRetriedRef.current.add(key);
          setTimeout(() => {
            void callTool("load_governance_overlays", { view_id: viewId }).catch(warn);
          }, 5000);
        }
      }).catch(warn);
    }, 800);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overlayKey, state?.section]);

  if (!view || !state) {
    return <div className="gov-loading">Loading Governance Explorer…</div>;
  }

  const viewId = view.view_id;

  const apply = (section: GovSectionId, draftOverride?: GovFilterDraft, forceRefresh?: boolean) => {
    loader.enqueue({
      __tool: "load_governance_section",
      ...buildSectionToolArgs(viewId, 0, section, draftOverride ?? draft, forceRefresh),
    });
  };
  const loadEntity = (entityType: GovEntityType, identifier: string) => {
    if (!identifier) return;
    loader.enqueue(entityCall(viewId, entityType, identifier));
  };
  const submitSearch = () => {
    if (!search.trim()) return;
    loader.enqueue({ __tool: "search_governance", ...buildSearchArgs(viewId, 0, search.trim()) });
  };
  const retryGroup = (section: string, group: string) => {
    groupLoader.retry(section, group, state.scope_id);
    groupLoader.sync(viewId, section, [group], state.scope_id);
  };

  // Treasury entities belong to Treasury even from a cold link (where there
  // is no previous section and the fallback would read "← Overview").
  const activeSection: GovSectionId = returnSectionFor(state, previousSection.current);
  const persistentWarnings = resolveWarnings(state);
  const trail = trailForDisplay(state.breadcrumbs ?? []);

  const ctx: GovViewContext = {
    state,
    descriptors,
    hydrated,
    viewId,
    fetchRows,
    draft,
    setDraft,
    apply: (section, draftOverride) => apply(section, draftOverride),
    loading: loader.loading,
    onEntity: loadEntity,
    failedGroups: groupLoader.failedGroups(state.scope_id),
    retryGroup,
    openLink: (url) => void openLink(url),
    sendMessage,
    aggregates,
    treasury: { view: treasuryView, update: updateTreasuryView, now },
  };

  const controls = (
    <div className="gov-controls">
      <button
        type="button"
        disabled={loader.loading}
        onClick={() => apply(activeSection, undefined, true)}
        title="Bypass caches and reload the current section"
      >
        ↻ Refresh
      </button>
    </div>
  );

  const subBar = (
    <div className="gov-subbar">
      <TabBar<GovSectionId>
        ariaLabel="Governance sections"
        tabs={SECTIONS.map((section) => ({ id: section.id, label: section.label }))}
        active={activeSection}
        onChange={(section) => apply(section)}
      />
      <MaSearchInput
        ariaLabel="Universal governance search"
        value={search}
        onChange={setSearch}
        onSubmit={submitSearch}
        placeholder="Proposal id, address, GIP number, topic, username"
        actionLabel="Search"
        busy={loader.loading}
        actionDisabled={!search.trim()}
      />
    </div>
  );

  const entityView = () => {
    switch (state.selected_entity?.entity_type) {
      case "proposal":
        return <ProposalDetail ctx={ctx} />;
      case "voter":
        return <VoterDetail ctx={ctx} />;
      case "forum_topic":
        return <TopicDetail ctx={ctx} />;
      case "forum_user":
        return <ContributorDetail ctx={ctx} />;
      case "treasury_token":
        return <TreasuryTokenDetail ctx={ctx} />;
      case "treasury_wallet":
        return <TreasuryWalletDetail ctx={ctx} />;
      default:
        return <div className="gov-empty">No entity selected.</div>;
    }
  };

  // The graph tab is the app's only full-canvas section, and three separate
  // layout decisions key off it. Named once so they cannot drift apart.
  const isGraph = state.section === "graph";
  const isTreasury = isTreasuryContext(state);

  return (
    <MiniAppChrome
      activeTabId="governance"
      rightSlot={controls}
      subBar={subBar}
      // The graph tab is a full-height canvas: it opts into the sanctioned
      // flush body (no padding, no page scroll) that graph-explorer and
      // model-lineage already use, and sizes itself from the flex chain.
      bodyClassName={isGraph ? "gov-body gov-body--flush" : "gov-body"}
    >
      {/* Skipped entirely on `graph`, not just its FreshnessStrip: the strip's
          own shell, padding and border cost ~29px that a full-height canvas
          cannot spare. Provenance lives on the Overview's expanded strip, on
          every panel's source label, and behind the graph toolbar's View SQL. */}
      {!isGraph && (
        <div className="gov-statusline">
          {isTreasury ? (
            <>
              {/* Treasury provenance, not the Snapshot/forum clocks: those
                  describe different sources and would read as the treasury's
                  freshness. Each chain's as-of sits in the treasury toolbar. */}
              <span>GnosisDAO treasury</span>
              <span>{PROVENANCE_LINE}</span>
              <span>{loader.loading ? "Loading…" : "Ready"}</span>
            </>
          ) : (
            <>
              <span>Gnosis DAO governance</span>
              <span>Snapshot signaling + forum activity — not binding execution</span>
              <span>{loader.loading ? "Loading…" : "Ready"}</span>
              {state.section !== "overview" && (
                <FreshnessStrip freshness={state.freshness} />
              )}
            </>
          )}
        </div>
      )}
      {state.search.candidates.length > 0 && (
        <div className="gov-candidates">
          <strong>Choose a match</strong>
          {state.search.candidates.map((candidate) => (
            <button
              key={`${candidate.entity_type}-${candidate.identifier}`}
              type="button"
              onClick={() => loadEntity(candidate.entity_type, candidate.identifier)}
            >
              <span>{candidate.role.split("_").join(" ")}</span>
              <code title={candidate.identifier}>{candidate.label || candidate.identifier}</code>
              <span>{candidate.evidence_count.toLocaleString()} matches</span>
            </button>
          ))}
        </div>
      )}
      {(state.section === "entity" || trail.length > 0) && (
        <div className="gov-breadcrumbs">
          <button
            type="button"
            onClick={() => loader.enqueue(sectionReturnCall(viewId, activeSection, draft))}
          >
            ← {SECTIONS.find((section) => section.id === activeSection)?.label ?? "Back"}
          </button>
          {trail.map((crumb, index) => {
            const isCurrent =
              state.section === "entity"
              && index === trail.length - 1
              && state.selected_entity?.identifier === crumb.identifier;
            return (
              <span key={`${crumb.entity_type}-${crumb.identifier}`} className="gov-crumb-item">
                <span className="gov-crumb-sep">/</span>{" "}
                {isCurrent ? (
                  <span className="is-current">{crumb.label || crumb.identifier}</span>
                ) : (
                  <button type="button" onClick={() => loader.enqueue(crumbCall(viewId, crumb))}>
                    {crumb.label || crumb.identifier}
                  </button>
                )}
              </span>
            );
          })}
        </div>
      )}
      {persistentWarnings.length > 0 && (
        <div className="gov-warn-strip" role="status">
          {persistentWarnings.map((warning, index) => (
            <span key={`${index}-${warning.slice(0, 24)}`} className="gov-warn-chip">{warning}</span>
          ))}
        </div>
      )}
      <main className={isGraph ? "gov-content gov-content--flush" : "gov-content"}>
        {state.section === "entity" ? (
          entityView()
        ) : state.section === "overview" ? (
          <OverviewSection ctx={ctx} />
        ) : state.section === "proposals" ? (
          <ProposalsSection ctx={ctx} />
        ) : state.section === "voters" ? (
          <VotersSection ctx={ctx} />
        ) : state.section === "forum" ? (
          <ForumSection ctx={ctx} />
        ) : state.section === "graph" ? (
          <GraphSection ctx={ctx} />
        ) : state.section === "treasury" ? (
          <TreasurySection ctx={ctx} />
        ) : (
          <DelegationsSection ctx={ctx} />
        )}
      </main>
      <ToastStack warnings={loader.error ? [loader.error] : []} autoDismissMs={0} />
    </MiniAppChrome>
  );
}
