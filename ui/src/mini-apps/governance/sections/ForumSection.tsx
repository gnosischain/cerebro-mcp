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
import { activityComboOption } from "../model/chartOptions";
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
              { label: "Likes", value: fmtNum(pickNumber(summary, ["like_count", "likes"])) },
              { label: "Active categories", value: fmtNum(pickNumber(summary, ["active_categories", "category_count"])) },
            ]}
          />
        )}
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
                if (column !== "username") return;
                const id = row[contributorIndex.get("user_id") ?? -1] ?? row[contributorIndex.get("id") ?? -1];
                if (id !== undefined && id !== null && id !== "") ctx.onEntity("forum_user", String(id));
              }}
              renderCell={(column, value) => {
                if (column === "last_post_at") {
                  return <span className="gov-mono">{String(value ?? "").slice(0, 10)}</span>;
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
