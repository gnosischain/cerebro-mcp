import { useMemo } from "react";
import { ChartCard } from "../../../components/ChartCard";
import { MaIdentity } from "../../shared/MiniAppChrome";
import { PaginatedTable } from "../../shared/PaginatedTable";
import { AskCerebroButton } from "../components/AskCerebroButton";
import { DatasetPanel } from "../components/DatasetPanel";
import { ExportCsvButton } from "../components/ExportCsvButton";
import { SignalingNote } from "../components/SignalingNote";
import { activityComboOption } from "../model/chartOptions";
import { COLUMN_LABELS, hiddenColumnsFor } from "../model/columns";
import { parseActivity } from "../model/parseRows";
import { firstRow, fmtNum, KpiRow, pickNumber, useDataset, type GovViewContext } from "../sections/common";

// Forum contributor profile. No wallet linkage, ever — forum identities are
// never merged with Snapshot voter addresses.

export function ContributorDetail({ ctx }: { ctx: GovViewContext }) {
  const entity = ctx.state.selected_entity;
  const profile = firstRow(ctx, "contributor_profile");
  const postsDescriptor = ctx.descriptors.contributor_posts;
  const postIndex = new Map((postsDescriptor?.columns ?? []).map((column, index) => [column.name, index]));
  const activityDs = useDataset(ctx, "contributor_activity");
  const activitySpec = useMemo(() => activityComboOption(parseActivity(activityDs), [
    { field: "post_count", label: "Posts", type: "bar" },
    { field: "topics_started", label: "Topics started", type: "line" },
  ]), [activityDs]);
  // De-identified handle (WL-039): the profile payload carries no username —
  // the stable id IS the identity here. Names appear only on verbatim-post
  // surfaces (TopicDetail), where showing the author as published is
  // attribution.
  const userId = pickNumber(profile, ["user_id"]);
  const handle = `User #${userId ?? entity?.identifier ?? "?"}`;

  return (
    <div className="gov-entity">
      <MaIdentity
        label="FORUM CONTRIBUTOR · forum.gnosis.io"
        value={handle}
        onCopy={() => void navigator.clipboard?.writeText(handle)}
      />

      <DatasetPanel title="Contributor profile" descriptor={ctx.descriptors.contributor_profile} groupLoaded emptyLabel="Contributor not found.">
        <KpiRow
          items={[
            { label: "Trust level", value: fmtNum(pickNumber(profile, ["trust_level"])) },
            { label: "Lifetime posts", value: fmtNum(pickNumber(profile, ["lifetime_posts", "post_count", "posts"])) },
            { label: "Lifetime topics", value: fmtNum(pickNumber(profile, ["lifetime_topics", "topic_count", "topics"])) },
            { label: "Likes given", value: fmtNum(pickNumber(profile, ["likes_given"])) },
            { label: "Likes received", value: fmtNum(pickNumber(profile, ["likes_received"])) },
            { label: "Days visited", value: fmtNum(pickNumber(profile, ["days_visited"])) },
          ]}
        />
        <p className="gov-caption">Forum identities are never linked to Snapshot wallet addresses.</p>
      </DatasetPanel>

      <DatasetPanel
        title="Posting activity"
        descriptor={ctx.descriptors.contributor_activity}
        groupLoaded
        hydrationPhase={ctx.hydrated.contributor_activity?.phase}
        hydrationError={ctx.hydrated.contributor_activity?.error}
      >
        <ChartCard
          chartId="gov-contributor-activity"
          hideId
          sql={ctx.descriptors.contributor_activity?.sql}
          sourceModel="governance_db"
          spec={activitySpec}
        />
      </DatasetPanel>

      <DatasetPanel title="Posts" descriptor={postsDescriptor} groupLoaded emptyLabel="No posts recorded.">
        <PaginatedTable
          dataset={postsDescriptor}
          datasetKey="contributor_posts"
          viewId={ctx.viewId}
          fetchRows={ctx.fetchRows}
          maxHeight="520px"
          hiddenColumns={hiddenColumnsFor("contributor_posts")}
          columnLabels={COLUMN_LABELS}
          sourceLabel="Forum activity (forum.gnosis.io)"
          onCellClick={(column, _value, row) => {
            if (column !== "topic_title" && column !== "title") return;
            const id = row[postIndex.get("topic_id") ?? -1];
            if (id !== undefined && id !== null && id !== "") ctx.onEntity("forum_topic", String(id));
          }}
          renderCell={(column, value) => {
            if (column === "created_at") {
              return <span className="gov-mono">{String(value ?? "").slice(0, 16).replace("T", " ")}</span>;
            }
            return undefined;
          }}
        />
      </DatasetPanel>

      <div className="gov-actions">
        <ExportCsvButton
          viewId={ctx.viewId}
          datasetKey="contributor_posts"
          descriptor={postsDescriptor}
          fetchRows={ctx.fetchRows}
          scope={`contributor_${entity?.identifier ?? userId ?? "unknown"}`}
          label="Export posts CSV"
          excludeColumns={["raw_markdown", "raw", "cooked_html", "cooked"]}
        />
        <AskCerebroButton state={ctx.state} aggregates={ctx.aggregates} sendMessage={ctx.sendMessage} />
      </div>
      <SignalingNote />
    </div>
  );
}
