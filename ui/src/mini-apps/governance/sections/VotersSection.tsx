import { useMemo, useState } from "react";
import { ChartCard } from "../../../components/ChartCard";
import { PaginatedTable } from "../../shared/PaginatedTable";
import { SegmentedControl } from "../../shared/SegmentedControl";
import { DatasetPanel, GroupBanner } from "../components/DatasetPanel";
import { activityComboOption, concentrationOption } from "../model/chartOptions";
import { COLUMN_LABELS, hiddenColumnsFor } from "../model/columns";
import { parseActivity, parseConcentration } from "../model/parseRows";
import { shortAddr } from "../../../utils/format";
import { fmtNum, fmtPct, firstRow, GroupGate, KpiRow, pickNumber, useDataset, type GovViewContext } from "./common";

// Voters: Snapshot signaling addresses only — never merged with forum
// identities. Concentration toggles between top-N tiers by voting power and
// by vote count.

export function VotersSection({ ctx }: { ctx: GovViewContext }) {
  const groups = ctx.state.loaded_groups ?? {};
  const summary = firstRow(ctx, "voter_summary");
  const [metric, setMetric] = useState<"vp" | "votes">("vp");

  const concentrationDs = useDataset(ctx, "voter_concentration");
  const concentrationSpec = useMemo(() => concentrationOption(
    parseConcentration(concentrationDs).filter((row) => row.metric === metric),
    metric === "vp" ? "VP share" : "Vote share",
  ), [concentrationDs, metric]);
  const activityDs = useDataset(ctx, "voter_activity");
  const activitySpec = useMemo(() => activityComboOption(parseActivity(activityDs), [
    { field: "unique_voters", label: "Unique voters", type: "bar" },
    { field: "vote_count", label: "Votes", type: "line", yAxisIndex: 1 },
  ], "votes"), [activityDs]);
  const leaderboard = ctx.descriptors.voter_leaderboard;
  const colIndex = new Map((leaderboard?.columns ?? []).map((column, index) => [column.name, index]));
  const retryInsights = () => ctx.retryGroup("voters", "insights");

  return (
    <>
      <GroupGate ctx={ctx} section="voters" group="core">
        {summary && (
          <KpiRow
            items={[
              { label: "Unique voters", value: fmtNum(pickNumber(summary, ["unique_voters", "voter_count"])) },
              { label: "Total VP cast", value: fmtNum(pickNumber(summary, ["total_vp", "vp_total"])) },
              { label: "Avg proposals / voter", value: fmtNum(pickNumber(summary, ["avg_participation", "avg_votes_per_voter"])) },
              { label: "Median proposals / voter", value: fmtNum(pickNumber(summary, ["median_participation", "median_votes_per_voter"])) },
              { label: "Repeat-voter rate", value: fmtPct(pickNumber(summary, ["repeat_rate", "repeat_voter_rate"])) },
            ]}
          />
        )}
        <DatasetPanel
          title="Voter leaderboard"
          descriptor={leaderboard}
          groupLoaded={groups["voters.core"]}
          onRetry={() => ctx.retryGroup("voters", "core")}
          emptyLabel="No voters in the selected range."
        >
          <PaginatedTable
            dataset={leaderboard}
            datasetKey="voter_leaderboard"
            viewId={ctx.viewId}
            fetchRows={ctx.fetchRows}
            maxHeight="520px"
            hiddenColumns={hiddenColumnsFor("voter_leaderboard")}
            columnLabels={COLUMN_LABELS}
            sourceLabel="Snapshot off-chain signaling"
            onCellClick={(column, value, row) => {
              if (column !== "voter") return;
              const voter = String(value ?? row[colIndex.get("voter") ?? -1] ?? "");
              if (voter) ctx.onEntity("voter", voter.toLowerCase());
            }}
            renderCell={(column, value) => {
              if (column === "voter") {
                return <span className="gov-mono" title={String(value ?? "")}>{shortAddr(String(value ?? ""))}</span>;
              }
              if (column === "first_vote_at" || column === "last_vote_at") {
                return <span className="gov-mono">{String(value ?? "").slice(0, 10)}</span>;
              }
              return undefined;
            }}
          />
        </DatasetPanel>
        <p className="gov-caption">Snapshot addresses are never merged with forum identities — voter and contributor profiles are separate by design.</p>
      </GroupGate>

      <GroupGate ctx={ctx} section="voters" group="insights">
        <GroupBanner groupLoaded={groups["voters.insights"]} onRetry={retryInsights} />
        <div className="gov-grid-2">
          <DatasetPanel
            title="Concentration (top voters)"
            descriptor={ctx.descriptors.voter_concentration}
            groupLoaded={groups["voters.insights"]}
            hydrationPhase={ctx.hydrated.voter_concentration?.phase}
            hydrationError={ctx.hydrated.voter_concentration?.error}
            onRetry={retryInsights}
            meta={
              <SegmentedControl<"vp" | "votes">
                ariaLabel="Concentration metric"
                size="sm"
                value={metric}
                options={[
                  { value: "vp", label: "By VP", ariaLabel: "Share of voting power" },
                  { value: "votes", label: "By votes", ariaLabel: "Share of votes cast" },
                ]}
                onChange={setMetric}
              />
            }
          >
            <ChartCard
              chartId="gov-voter-concentration"
              hideId
              sql={ctx.descriptors.voter_concentration?.sql}
              sourceModel="governance_db"
              spec={concentrationSpec}
            />
          </DatasetPanel>
          <DatasetPanel
            title="Voter activity"
            descriptor={ctx.descriptors.voter_activity}
            groupLoaded={groups["voters.insights"]}
            hydrationPhase={ctx.hydrated.voter_activity?.phase}
            hydrationError={ctx.hydrated.voter_activity?.error}
            onRetry={retryInsights}
          >
            <ChartCard
              chartId="gov-voter-activity"
              hideId
              sql={ctx.descriptors.voter_activity?.sql}
              sourceModel="governance_db"
              spec={activitySpec}
            />
          </DatasetPanel>
        </div>
      </GroupGate>
    </>
  );
}
