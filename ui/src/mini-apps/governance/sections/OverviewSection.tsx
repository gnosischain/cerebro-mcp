import { useMemo } from "react";
import { ChartCard } from "../../../components/ChartCard";
import { DatasetPanel, GroupBanner } from "../components/DatasetPanel";
import { FreshnessStrip } from "../components/FreshnessStrip";
import {
  activityComboOption,
  concentrationOption,
  categoryCountOption,
  donutOption,
  horizontalBarOption,
} from "../model/chartOptions";
import { rowsToObjects } from "../../shared/rowDataset";
import { parseActivity } from "../model/parseRows";
import { dataset, fmtNum, firstRow, GroupGate, KpiRow, pickNumber, pickString, useDataset, type GovViewContext } from "./common";

// Overview: 7 headline KPIs, the expanded dual-clock freshness strip, five
// insight charts, and two latest-activity click-through feeds.

const KPI_DEFS: Array<{ label: string; keys: string[] }> = [
  { label: "Proposals", keys: ["proposal_count", "proposals"] },
  { label: "Votes", keys: ["vote_count", "votes"] },
  { label: "Unique voters", keys: ["voter_count", "unique_voters"] },
  { label: "Followers", keys: ["follower_count", "followers"] },
  { label: "Forum topics", keys: ["topic_count", "topics"] },
  { label: "Forum posts", keys: ["post_count", "posts"] },
  { label: "Forum users (all-time)", keys: ["forum_user_count", "forum_users"] },
];

function FeedRow({ time, title, onOpen }: { time: string; title: string; onOpen: () => void }) {
  return (
    <li className="gov-feed__row">
      <span className="gov-feed__time">{time.slice(0, 10)}</span>
      <button type="button" className="gov-feed__main" title={title} onClick={onOpen}>{title}</button>
    </li>
  );
}

export function OverviewSection({ ctx }: { ctx: GovViewContext }) {
  const groups = ctx.state.loaded_groups ?? {};
  const summary = firstRow(ctx, "space_summary");

  const activityDs = useDataset(ctx, "governance_activity");
  const activitySpec = useMemo(() => activityComboOption(parseActivity(activityDs), [
    { field: "proposals_created", label: "Proposals", type: "bar" },
    { field: "topics_created", label: "Topics", type: "bar" },
    { field: "posts_created", label: "Posts", type: "line" },
    { field: "votes_cast", label: "Votes", type: "line", yAxisIndex: 1 },
  ], "votes"), [activityDs]);

  const typesDs = useDataset(ctx, "proposal_types");
  const typesSpec = useMemo(() => donutOption(rowsToObjects(typesDs)
    .map((row) => ({
      name: pickString(row, ["type", "proposal_type", "name"]) || "unknown",
      value: pickNumber(row, ["count", "proposal_count", "proposals", "value"]) ?? 0,
    }))
    .filter((row) => row.value > 0)), [typesDs]);

  const quorumDs = useDataset(ctx, "quorum_distribution");
  const quorumSpec = useMemo(() => categoryCountOption(rowsToObjects(quorumDs)
    .map((row) => ({
      name: pickString(row, ["quorum_status", "status", "name"]) || "unknown",
      value: pickNumber(row, ["count", "proposal_count", "proposals", "value"]) ?? 0,
    })), "Proposals"), [quorumDs]);

  const concentrationDs = useDataset(ctx, "voter_power_concentration");
  const concentrationSpec = useMemo(() => concentrationOption(rowsToObjects(concentrationDs)
    .filter((row) => {
      const metric = pickString(row, ["metric"]);
      return metric === "" || metric === "vp";
    })
    .map((row) => ({
      tier: pickNumber(row, ["tier", "top_n"]) ?? 0,
      share: pickNumber(row, ["share", "vp_share"]),
    }))
    .filter((row) => row.tier > 0), "VP share"), [concentrationDs]);

  const categoryDs = useDataset(ctx, "forum_category_activity");
  const categorySpec = useMemo(() => horizontalBarOption(rowsToObjects(categoryDs)
    .map((row) => ({
      name: pickString(row, ["category_name", "name", "category"]) || "unknown",
      value: pickNumber(row, ["posts_in_range", "post_count", "posts", "topics_in_range", "topic_count", "value"]) ?? 0,
    }))
    .filter((row) => row.value > 0), "Posts"), [categoryDs]);

  const latest = rowsToObjects(dataset(ctx, "latest_activity"));
  const kindOf = (row: Record<string, unknown>) =>
    pickString(row, ["kind", "source", "entity_type"]).toLowerCase();
  const latestProposals = latest.filter((row) => kindOf(row).includes("proposal") || kindOf(row) === "snapshot");
  const latestTopics = latest.filter((row) => kindOf(row).includes("topic") || kindOf(row) === "forum");

  const retryInsights = () => ctx.retryGroup("overview", "insights");

  return (
    <>
      <GroupGate ctx={ctx} section="overview" group="core">
        <KpiRow
          items={KPI_DEFS.map((def) => ({
            label: def.label,
            value: fmtNum(pickNumber(summary, def.keys)),
          }))}
        />
        <FreshnessStrip freshness={ctx.state.freshness} expanded />
        <DatasetPanel
          title="Governance activity"
          descriptor={ctx.descriptors.governance_activity}
          groupLoaded={groups["overview.core"]}
          hydrationPhase={ctx.hydrated.governance_activity?.phase}
          hydrationError={ctx.hydrated.governance_activity?.error}
          onRetry={() => ctx.retryGroup("overview", "core")}
        >
          <ChartCard
            chartId="gov-activity"
            hideId
            sql={ctx.descriptors.governance_activity?.sql}
            sourceModel="governance_db"
            spec={activitySpec}
          />
        </DatasetPanel>
      </GroupGate>

      <GroupGate ctx={ctx} section="overview" group="insights">
        <GroupBanner groupLoaded={groups["overview.insights"]} onRetry={retryInsights} />
        <div className="gov-grid-2">
          <DatasetPanel
            title="Proposal types"
            descriptor={ctx.descriptors.proposal_types}
            groupLoaded={groups["overview.insights"]}
            hydrationPhase={ctx.hydrated.proposal_types?.phase}
            hydrationError={ctx.hydrated.proposal_types?.error}
            onRetry={retryInsights}
          >
            <ChartCard chartId="gov-types" hideId sql={ctx.descriptors.proposal_types?.sql} sourceModel="governance_db" spec={typesSpec} />
          </DatasetPanel>
          <DatasetPanel
            title="Quorum attainment"
            descriptor={ctx.descriptors.quorum_distribution}
            groupLoaded={groups["overview.insights"]}
            hydrationPhase={ctx.hydrated.quorum_distribution?.phase}
            hydrationError={ctx.hydrated.quorum_distribution?.error}
            onRetry={retryInsights}
          >
            <ChartCard chartId="gov-quorum" hideId sql={ctx.descriptors.quorum_distribution?.sql} sourceModel="governance_db" spec={quorumSpec} />
          </DatasetPanel>
          <DatasetPanel
            title="Voting-power concentration"
            descriptor={ctx.descriptors.voter_power_concentration}
            groupLoaded={groups["overview.insights"]}
            hydrationPhase={ctx.hydrated.voter_power_concentration?.phase}
            hydrationError={ctx.hydrated.voter_power_concentration?.error}
            onRetry={retryInsights}
          >
            <ChartCard chartId="gov-concentration" hideId sql={ctx.descriptors.voter_power_concentration?.sql} sourceModel="governance_db" spec={concentrationSpec} />
          </DatasetPanel>
          <DatasetPanel
            title="Forum category activity"
            descriptor={ctx.descriptors.forum_category_activity}
            groupLoaded={groups["overview.insights"]}
            hydrationPhase={ctx.hydrated.forum_category_activity?.phase}
            hydrationError={ctx.hydrated.forum_category_activity?.error}
            onRetry={retryInsights}
          >
            <ChartCard chartId="gov-categories" hideId sql={ctx.descriptors.forum_category_activity?.sql} sourceModel="governance_db" spec={categorySpec} />
          </DatasetPanel>
        </div>
        <div className="gov-grid-2">
          <DatasetPanel
            title="Recent proposals"
            descriptor={ctx.descriptors.latest_activity}
            groupLoaded={groups["overview.insights"]}
            onRetry={retryInsights}
            emptyLabel="No recent proposals."
          >
            <ul className="gov-feed">
              {latestProposals.map((row, index) => (
                <FeedRow
                  key={`p-${index}`}
                  time={pickString(row, ["activity_at", "created_at", "ts", "bucket"])}
                  title={pickString(row, ["title", "label"]) || pickString(row, ["identifier", "id"])}
                  onOpen={() => {
                    const id = pickString(row, ["identifier", "id", "proposal_id"]);
                    if (id) ctx.onEntity("proposal", id);
                  }}
                />
              ))}
            </ul>
          </DatasetPanel>
          <DatasetPanel
            title="Active discussions"
            descriptor={ctx.descriptors.latest_activity}
            groupLoaded={groups["overview.insights"]}
            onRetry={retryInsights}
            emptyLabel="No recently active discussions."
          >
            <ul className="gov-feed">
              {latestTopics.map((row, index) => (
                <FeedRow
                  key={`t-${index}`}
                  time={pickString(row, ["activity_at", "last_posted_at", "ts", "bucket"])}
                  title={pickString(row, ["title", "label"]) || pickString(row, ["identifier", "id"])}
                  onOpen={() => {
                    const id = pickString(row, ["identifier", "id", "topic_id"]);
                    if (id) ctx.onEntity("forum_topic", id);
                  }}
                />
              ))}
            </ul>
          </DatasetPanel>
        </div>
      </GroupGate>
    </>
  );
}
