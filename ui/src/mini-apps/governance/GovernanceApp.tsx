import { useEffect, useMemo, useRef, useState } from "react";
import { MiniAppChrome } from "../shared/MiniAppChrome";
import { TabBar } from "../shared/TabBar";
import { ToastStack } from "../shared/ToastStack";
import { WarningBanner } from "../shared/WarningBanner";
import { MaSearchInput } from "../shared/MaSearchInput";
import { useGroupLoader } from "../shared/useGroupLoader";
import { useHydratedDatasets } from "../shared/useHydratedDatasets";
import { useMiniApp } from "../shared/useMiniApp";
import { useSerializedLoader } from "../shared/useSerializedLoader";
import { FreshnessStrip } from "./components/FreshnessStrip";
import { ContributorDetail } from "./detail/ContributorDetail";
import { ProposalDetail } from "./detail/ProposalDetail";
import { TopicDetail } from "./detail/TopicDetail";
import { VoterDetail } from "./detail/VoterDetail";
import { MOCK_PAYLOAD } from "./devFixture";
import { buildModelContextLines, type GovAggregates } from "./model/contextPrompt";
import { parseSpaceSummary } from "./model/parseRows";
import { ForumSection } from "./sections/ForumSection";
import { OverviewSection } from "./sections/OverviewSection";
import { ProposalsSection } from "./sections/ProposalsSection";
import { VotersSection } from "./sections/VotersSection";
import type { GovViewContext } from "./sections/common";
import {
  crumbCall,
  entityCall,
  sectionReturnCall,
  seedCall,
  trailForDisplay,
  type GovSectionId,
} from "./state/navigation";
import { buildSearchArgs, buildSectionToolArgs, EMPTY_DRAFT, type GovFilterDraft } from "./state/toolArgs";
import type { GovEntityType, GovernanceViewState } from "./types";
import { readUrl, writeUrl, type GovUrlState } from "./urlState";

const APP_ID = "governance";

const SECTIONS: Array<{ id: GovSectionId; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "proposals", label: "Proposals" },
  { id: "voters", label: "Voters" },
  { id: "forum", label: "Forum" },
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
]);

/** Frozen warning-code vocabulary → user copy. Unknown strings (human
 * messages with spaces) pass through unchanged. */
const WARNING_COPY: Record<string, string> = {
  query_failed: "One dataset failed to load; other successful datasets remain available.",
  no_data: "No matching rows were found for the selected filters.",
  stale_scope: "A background load arrived for a superseded view and was ignored.",
  result_truncated: "A result is capped at the newest exact 10,000 rows; narrow filters for the full set.",
  source_stale: "The latest ingestion for at least one source is older than 24 hours.",
  unsupported_choice_shape: "Some votes carry an unsupported choice shape; they are flagged, never interpreted.",
};

function resolveWarnings(state: GovernanceViewState): string[] {
  return [...new Set([...(state.coverage_warnings ?? []), ...(state.warnings ?? [])])]
    .map((warning) => WARNING_COPY[warning] ?? warning);
}

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
      mockPayload: import.meta.env.DEV ? MOCK_PAYLOAD : undefined,
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
  // One-shot URL deep-link seed (standalone mode): consumed by the FIRST
  // section apply; `entity`+`id` params short-circuit to the entity load.
  const urlSeedRef = useRef<GovUrlState | null | undefined>(undefined);
  if (urlSeedRef.current === undefined) {
    urlSeedRef.current = typeof window !== "undefined" ? readUrl() : null;
  }

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
    if (typeof window !== "undefined" && window.__MINI_APP_API__) writeUrl(state);
    updateModelContext(buildModelContextLines(state, aggregates));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.scope_id, state?.applied_request_id]);

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

  const activeSection: GovSectionId = state.section === "entity" ? previousSection.current : state.section;
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
      default:
        return <div className="gov-empty">No entity selected.</div>;
    }
  };

  return (
    <MiniAppChrome activeTabId="governance" rightSlot={controls} subBar={subBar} bodyClassName="gov-body">
      <div className="gov-statusline">
        <span>Gnosis DAO governance</span>
        <span>Snapshot signaling + forum activity — not binding execution</span>
        <span>{loader.loading ? "Loading…" : "Ready"}</span>
        {state.section !== "overview" && <FreshnessStrip freshness={state.freshness} />}
      </div>
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
            onClick={() => loader.enqueue(sectionReturnCall(viewId, previousSection.current, draft))}
          >
            ← {SECTIONS.find((section) => section.id === previousSection.current)?.label ?? "Back"}
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
      <WarningBanner warnings={persistentWarnings} />
      <main className="gov-content">
        {state.section === "entity" ? (
          entityView()
        ) : state.section === "overview" ? (
          <OverviewSection ctx={ctx} />
        ) : state.section === "proposals" ? (
          <ProposalsSection ctx={ctx} />
        ) : state.section === "voters" ? (
          <VotersSection ctx={ctx} />
        ) : (
          <ForumSection ctx={ctx} />
        )}
      </main>
      <ToastStack warnings={loader.error ? [loader.error] : []} autoDismissMs={0} />
    </MiniAppChrome>
  );
}
