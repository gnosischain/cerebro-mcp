import { useMemo } from "react";
import { ChartCard } from "../../../components/ChartCard";
import { AsyncButton } from "../../shared/AsyncButton";
import { MaField } from "../../shared/MaField";
import { MaToolbar } from "../../shared/MaToolbar";
import { PaginatedTable } from "../../shared/PaginatedTable";
import { SegmentedControl } from "../../shared/SegmentedControl";
import { DatasetPanel, GroupBanner } from "../components/DatasetPanel";
import { DateRangeControl } from "../components/DateRangeControl";
import { GipBadge } from "../components/GipBadge";
import { activityComboOption, likesByCategoryOption } from "../model/chartOptions";
import { COLUMN_LABELS, hiddenColumnsFor } from "../model/columns";
import { rowsToObjects } from "../../shared/rowDataset";
import { parseActivity } from "../model/parseRows";
import { dataset, fmtNum, firstRow, GroupGate, KpiRow, pickNumber, pickString, useDataset, type GovViewContext } from "./common";

const FORUM_SORTS: Array<{ value: string; label: string }> = [
  { value: "", label: "Default (recent activity)" },
  { value: "recent_activity", label: "Recent activity" },
  { value: "newest", label: "Newest" },
  { value: "most_posts", label: "Most posts" },
  { value: "most_views", label: "Most views" },
  { value: "most_likes", label: "Most likes" },
];

export function ForumSection({ ctx }: { ctx: GovViewContext }) {
  const groups = ctx.state.loaded_groups ?? {};
  const summary = firstRow(ctx, "forum_summary");
  const categories = rowsToObjects(dataset(ctx, "forum_categories"));
  const topics = ctx.descriptors.forum_topics;
  const topicIndex = new Map((topics?.columns ?? []).map((column, index) => [column.name, index]));
  const contributors = ctx.descriptors.contributor_leaderboard;
  const contributorIndex = new Map((contributors?.columns ?? []).map((column, index) => [column.name, index]));
  const activityDs = useDataset(ctx, "forum_activity");
  const activitySpec = useMemo(() => activityComboOption(parseActivity(activityDs), [
    { field: "topics_created", label: "Topics", type: "bar" },
    { field: "posts_created", label: "Posts", type: "line", yAxisIndex: 1 },
  ], "posts"), [activityDs]);
  const retryInsights = () => ctx.retryGroup("forum", "insights");

  const pollSummary = firstRow(ctx, "poll_summary");
  const polls = ctx.descriptors.forum_polls;
  const pollIndex = new Map((polls?.columns ?? []).map((column, index) => [column.name, index]));
  const likedTopics = ctx.descriptors.most_liked_topics;
  const likedIndex = new Map((likedTopics?.columns ?? []).map((column, index) => [column.name, index]));
  const pollActivityDs = useDataset(ctx, "poll_activity");
  const pollActivitySpec = useMemo(() => activityComboOption(parseActivity(pollActivityDs), [
    { field: "polls_created", label: "Polls created", type: "bar" },
    { field: "poll_voters", label: "Poll voters", type: "line", yAxisIndex: 1 },
  ], "voters"), [pollActivityDs]);
  // Likes: bars stacked by topic category (likes_by_category) + the
  // unique-likers line (likes_activity) — same bucketing and filters, so the
  // buckets align by construction.
  const likesActivityDs = useDataset(ctx, "likes_activity");
  const likesByCategoryDs = useDataset(ctx, "likes_by_category");
  const likesActivitySpec = useMemo(() => {
    const likersByBucket = new Map<string, number | null>();
    for (const row of parseActivity(likesActivityDs)) {
      likersByBucket.set(row.bucket, typeof row.distinct_likers === "number" ? row.distinct_likers : null);
    }
    return likesByCategoryOption(rowsToObjects(likesByCategoryDs), likersByBucket);
  }, [likesActivityDs, likesByCategoryDs]);
  const retryEngagement = () => ctx.retryGroup("forum", "engagement");

  // Visible attribution disclosure — the per-like table only attributes a
  // share of the counter-tracked likes; the live figure ships in the summary
  // (like_attribution_pct), never hard-coded here.
  const attributionPct = pickNumber(summary, ["like_attribution_pct"]);
  const likesHiddenOrDeleted = pickNumber(summary, ["likes_hidden_or_deleted"]) ?? 0;
  const likesUnmapped = pickNumber(summary, ["likes_unmapped"]) ?? 0;
  const attributionCopy =
    `Attributed likes cover ${attributionPct !== null ? `${(attributionPct * 100).toFixed(0)}%` : "an unknown share"}`
    + " of counter-tracked likes, measured over all forum history as of the latest ingest —"
    + " coverage within the selected window is unknown (Discourse who-liked visibility limit).";
  const exclusionCopy = likesHiddenOrDeleted + likesUnmapped > 0
    ? ` ${fmtNum(likesHiddenOrDeleted)} hidden/deleted and ${fmtNum(likesUnmapped)} unmapped likes excluded.`
    : "";

  return (
    <>
      <MaToolbar className="gov-toolbar">
        <label>
          Category
          <select
            value={String(ctx.draft.category_id || 0)}
            onChange={(event) => ctx.setDraft((draft) => ({ ...draft, category_id: Number(event.target.value) || 0 }))}
          >
            <option value="0">All categories</option>
            {categories.map((category) => {
              const id = pickNumber(category, ["id", "category_id"]);
              const name = pickString(category, ["name", "category_name"]);
              const count = pickNumber(category, ["topic_count", "topics"]);
              if (id === null || !name) return null;
              return (
                <option key={id} value={String(id)}>
                  {name}{count !== null ? ` (${count})` : ""}
                </option>
              );
            })}
          </select>
        </label>
        <SegmentedControl<string>
          ariaLabel="Topic status"
          size="sm"
          value={ctx.draft.forum_status || "all"}
          options={[
            { value: "all", label: "All" },
            { value: "open", label: "Open" },
            { value: "closed", label: "Closed" },
            { value: "archived", label: "Archived" },
          ]}
          onChange={(next) => ctx.setDraft((draft) => ({ ...draft, forum_status: next === "all" ? "" : next }))}
        />
        <DateRangeControl draft={ctx.draft} onChange={(next) => ctx.setDraft(next)} />
        <MaField className="gov-query">
          <input
            type="text"
            aria-label="Topic text filter"
            placeholder="Filter topic titles…"
            value={ctx.draft.query}
            onChange={(event) => ctx.setDraft((draft) => ({ ...draft, query: event.target.value }))}
          />
        </MaField>
        <label>
          Sort
          <select
            value={ctx.draft.sort_by}
            onChange={(event) => ctx.setDraft((draft) => ({ ...draft, sort_by: event.target.value }))}
          >
            {FORUM_SORTS.map((sort) => <option key={sort.value} value={sort.value}>{sort.label}</option>)}
          </select>
        </label>
        <AsyncButton loadingLabel="Applying" disabled={ctx.loading} onClick={() => ctx.apply("forum")}>
          Apply
        </AsyncButton>
      </MaToolbar>

      <GroupGate ctx={ctx} section="forum" group="core">
        {summary && (
          <KpiRow
            items={[
              { label: "Topics (scope)", value: fmtNum(pickNumber(summary, ["topic_count", "topics"])) },
              { label: "Posts", value: fmtNum(pickNumber(summary, ["post_count", "posts"])) },
              { label: "Active contributors", value: fmtNum(pickNumber(summary, ["contributor_count", "active_users", "user_count"])) },
              { label: "Views", value: fmtNum(pickNumber(summary, ["views", "view_count"])) },
              { label: "Likes (lifetime)", value: fmtNum(pickNumber(summary, ["like_count", "likes"])) },
              { label: "Attributed likes (range)", value: fmtNum(pickNumber(summary, ["likes_in_range"])) },
              { label: "Likers (range)", value: fmtNum(pickNumber(summary, ["distinct_likers"])) },
              { label: "Active categories", value: fmtNum(pickNumber(summary, ["active_categories", "category_count"])) },
            ]}
          />
        )}
        {/* Below the grid, not in the meta slot: this copy is long, and the
            flex meta slot would squeeze the KPI grid into a single column. */}
        {summary && <p className="gov-caption">{attributionCopy}{exclusionCopy}</p>}
        <DatasetPanel
          title="Topics"
          descriptor={topics}
          groupLoaded={groups["forum.core"]}
          onRetry={() => ctx.retryGroup("forum", "core")}
          emptyLabel="No topics match the applied filters."
        >
          <PaginatedTable
            dataset={topics}
            datasetKey="forum_topics"
            viewId={ctx.viewId}
            fetchRows={ctx.fetchRows}
            maxHeight="560px"
            hiddenColumns={hiddenColumnsFor("forum_topics")}
            columnLabels={COLUMN_LABELS}
            sourceLabel="Forum activity (forum.gnosis.io)"
            onCellClick={(column, _value, row) => {
              if (column !== "title") return;
              const id = row[topicIndex.get("id") ?? -1];
              if (id !== undefined && id !== null && id !== "") ctx.onEntity("forum_topic", String(id));
            }}
            renderCell={(column, value, row) => {
              if (column === "title") {
                return (
                  <span>
                    {String(value ?? "")}
                    <GipBadge gip={pickNumber({ gip: row[topicIndex.get("gip_number") ?? -1] }, ["gip"])} />
                  </span>
                );
              }
              if (column === "status") {
                const status = String(value ?? "");
                return <span className={`gov-state-chip gov-state-chip--${status === "open" ? "active" : status}`}>{status || "—"}</span>;
              }
              if (column === "last_posted_at" || column === "created_at") {
                return <span className="gov-mono">{String(value ?? "").slice(0, 10)}</span>;
              }
              return undefined;
            }}
          />
        </DatasetPanel>
      </GroupGate>

      <GroupGate ctx={ctx} section="forum" group="insights">
        <GroupBanner groupLoaded={groups["forum.insights"]} onRetry={retryInsights} />
        <div className="gov-grid-2">
          <DatasetPanel
            title="Forum activity"
            descriptor={ctx.descriptors.forum_activity}
            groupLoaded={groups["forum.insights"]}
            hydrationPhase={ctx.hydrated.forum_activity?.phase}
            hydrationError={ctx.hydrated.forum_activity?.error}
            onRetry={retryInsights}
          >
            <ChartCard
              chartId="gov-forum-activity"
              hideId
              sql={ctx.descriptors.forum_activity?.sql}
              sourceModel="governance_db"
              spec={activitySpec}
            />
          </DatasetPanel>
          <DatasetPanel
            title="Contributor leaderboard"
            descriptor={contributors}
            groupLoaded={groups["forum.insights"]}
            onRetry={retryInsights}
            emptyLabel="No contributors in the selected range."
          >
            <PaginatedTable
              dataset={contributors}
              datasetKey="contributor_leaderboard"
              viewId={ctx.viewId}
              fetchRows={ctx.fetchRows}
              maxHeight="440px"
              hiddenColumns={hiddenColumnsFor("contributor_leaderboard")}
              columnLabels={COLUMN_LABELS}
              sourceLabel="Forum activity (forum.gnosis.io)"
              onCellClick={(column, _value, row) => {
                // Post-de-identification the User ID column IS the identity
                // column, so it carries the drill-through.
                if (column !== "user_id") return;
                const id = row[contributorIndex.get("user_id") ?? -1] ?? row[contributorIndex.get("id") ?? -1];
                if (id !== undefined && id !== null && id !== "") ctx.onEntity("forum_user", String(id));
              }}
              renderCell={(column, value) => {
                if (column === "user_id") {
                  return <span className="gov-mono">{String(value ?? "")}</span>;
                }
                if (column === "last_post_at") {
                  return <span className="gov-mono">{String(value ?? "").slice(0, 10)}</span>;
                }
                return undefined;
              }}
            />
            {/* This panel sits OUTSIDE the engagement gate, so it carries its
                own copy of the attribution disclosure. */}
            <p className="gov-caption">
              Likes received/given (range) are attributed likes only. {attributionCopy}
            </p>
          </DatasetPanel>
        </div>
      </GroupGate>

      <GroupGate ctx={ctx} section="forum" group="engagement">
        <GroupBanner groupLoaded={groups["forum.engagement"]} onRetry={retryEngagement} />
        {pollSummary && (
          <KpiRow
            items={[
              { label: "Polls", value: fmtNum(pickNumber(pollSummary, ["poll_count"])) },
              { label: "Open polls", value: fmtNum(pickNumber(pollSummary, ["open_polls"])) },
              { label: "Poll voters (slots)", value: fmtNum(pickNumber(pollSummary, ["poll_voter_slots"])) },
              { label: "Topics with polls", value: fmtNum(pickNumber(pollSummary, ["topics_with_polls"])) },
              { label: "Multiple-choice", value: fmtNum(pickNumber(pollSummary, ["multiple_choice_polls"])) },
              { label: "Hidden results", value: fmtNum(pickNumber(pollSummary, ["hidden_result_polls"])) },
            ]}
          />
        )}
        {pollSummary && (
          <p className="gov-caption">
            Poll voters (slots) sums each poll's participant total — a user voting in several
            polls counts once per poll. Like metrics below are attributed likes only. {attributionCopy}
          </p>
        )}
        <div className="gov-grid-2">
          <DatasetPanel
            title="Poll activity"
            descriptor={ctx.descriptors.poll_activity}
            groupLoaded={groups["forum.engagement"]}
            hydrationPhase={ctx.hydrated.poll_activity?.phase}
            hydrationError={ctx.hydrated.poll_activity?.error}
            onRetry={retryEngagement}
          >
            <ChartCard
              chartId="gov-poll-activity"
              hideId
              sql={ctx.descriptors.poll_activity?.sql}
              sourceModel="governance_db"
              spec={pollActivitySpec}
            />
            <p className="gov-caption">
              Polls and their participant totals grouped by the poll-bearing post's creation
              date, not vote time — Discourse records no per-vote timestamps.
            </p>
          </DatasetPanel>
          <DatasetPanel
            title="Likes activity"
            descriptor={ctx.descriptors.likes_by_category}
            groupLoaded={groups["forum.engagement"]}
            hydrationPhase={ctx.hydrated.likes_by_category?.phase}
            hydrationError={ctx.hydrated.likes_by_category?.error}
            onRetry={retryEngagement}
          >
            <ChartCard
              chartId="gov-likes-activity"
              hideId
              sql={ctx.descriptors.likes_by_category?.sql}
              sourceModel="governance_db"
              spec={likesActivitySpec}
            />
            <p className="gov-caption">
              Bars are likes given, stacked by the topic's forum category (top categories named,
              the rest counted as Other); the line is unique likers per period.
            </p>
          </DatasetPanel>
        </div>
        <div className="gov-grid-2">
          <DatasetPanel
            title="Polls"
            descriptor={polls}
            groupLoaded={groups["forum.engagement"]}
            onRetry={retryEngagement}
            emptyLabel="No polls match the applied filters."
          >
            <PaginatedTable
              dataset={polls}
              datasetKey="forum_polls"
              viewId={ctx.viewId}
              fetchRows={ctx.fetchRows}
              maxHeight="440px"
              hiddenColumns={hiddenColumnsFor("forum_polls")}
              columnLabels={COLUMN_LABELS}
              sourceLabel="Forum activity (forum.gnosis.io)"
              onCellClick={(column, _value, row) => {
                if (column !== "topic_title") return;
                const id = row[pollIndex.get("topic_id") ?? -1];
                if (id !== undefined && id !== null && id !== "") ctx.onEntity("forum_topic", String(id));
              }}
              renderCell={(column, value, row) => {
                if (column === "leading_option") {
                  if (row[pollIndex.get("results_hidden") ?? -1]) return <span>Hidden</span>;
                  if (row[pollIndex.get("leading_tied") ?? -1]) return <span>Tie</span>;
                  const votes = row[pollIndex.get("leading_votes") ?? -1];
                  if (Number(votes ?? 0) === 0) return <span>No votes</span>;
                  return <span>{String(value ?? "—")}</span>;
                }
                if (column === "status") {
                  const status = String(value ?? "");
                  return <span className={`gov-state-chip gov-state-chip--${status === "open" ? "active" : status}`}>{status || "—"}</span>;
                }
                if (column === "created_at" || column === "close_at") {
                  const text = String(value ?? "").slice(0, 10);
                  return <span className="gov-mono">{text || "—"}</span>;
                }
                return undefined;
              }}
            />
          </DatasetPanel>
          <DatasetPanel
            title="Most liked topics"
            descriptor={likedTopics}
            groupLoaded={groups["forum.engagement"]}
            onRetry={retryEngagement}
            emptyLabel="No attributed likes in the selected range."
          >
            <PaginatedTable
              dataset={likedTopics}
              datasetKey="most_liked_topics"
              viewId={ctx.viewId}
              fetchRows={ctx.fetchRows}
              maxHeight="440px"
              hiddenColumns={hiddenColumnsFor("most_liked_topics")}
              columnLabels={COLUMN_LABELS}
              sourceLabel="Forum activity (forum.gnosis.io)"
              onCellClick={(column, _value, row) => {
                if (column !== "title") return;
                const id = row[likedIndex.get("id") ?? -1];
                if (id !== undefined && id !== null && id !== "") ctx.onEntity("forum_topic", String(id));
              }}
              renderCell={(column, value, row) => {
                if (column === "title") {
                  return (
                    <span>
                      {String(value ?? "")}
                      <GipBadge gip={pickNumber({ gip: row[likedIndex.get("gip_number") ?? -1] }, ["gip"])} />
                    </span>
                  );
                }
                return undefined;
              }}
            />
          </DatasetPanel>
        </div>
      </GroupGate>
    </>
  );
}
