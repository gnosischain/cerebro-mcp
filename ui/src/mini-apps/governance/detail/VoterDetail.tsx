import { useMemo } from "react";
import { ChartCard } from "../../../components/ChartCard";
import { MaIdentity } from "../../shared/MiniAppChrome";
import { PaginatedTable } from "../../shared/PaginatedTable";
import { AskCerebroButton } from "../components/AskCerebroButton";
import { DatasetPanel } from "../components/DatasetPanel";
import { ExportCsvButton } from "../components/ExportCsvButton";
import { SignalingNote } from "../components/SignalingNote";
import { VoteChoiceCell } from "../components/VoteChoiceCell";
import { activityComboOption } from "../model/chartOptions";
import { COLUMN_LABELS, hiddenColumnsFor } from "../model/columns";
import { parseActivity } from "../model/parseRows";
import { firstRow, fmtNum, fmtPct, KpiRow, pickNumber, pickString, useDataset, type GovViewContext } from "../sections/common";

export function VoterDetail({ ctx }: { ctx: GovViewContext }) {
  const entity = ctx.state.selected_entity;
  const profile = firstRow(ctx, "voter_profile");
  const votes = ctx.descriptors.voter_votes;
  const voteIndex = new Map((votes?.columns ?? []).map((column, index) => [column.name, index]));
  const participationDs = useDataset(ctx, "voter_participation");
  const participationSpec = useMemo(() => activityComboOption(parseActivity(participationDs), [
    { field: "vote_count", label: "Votes", type: "bar" },
    { field: "total_vp", label: "VP", type: "line", yAxisIndex: 1 },
  ], "VP"), [participationDs]);
  const identifier = entity?.identifier ?? pickString(profile, ["voter"]);

  return (
    <div className="gov-entity">
      <MaIdentity
        label="VOTER · Snapshot off-chain signaling"
        value={identifier}
        onCopy={() => void navigator.clipboard?.writeText(identifier)}
      />

      <DatasetPanel title="Voter profile" descriptor={ctx.descriptors.voter_profile} groupLoaded emptyLabel="No votes recorded for this address.">
        <KpiRow
          items={[
            { label: "Votes cast", value: fmtNum(pickNumber(profile, ["vote_count", "votes"])) },
            { label: "Total VP cast", value: fmtNum(pickNumber(profile, ["total_vp", "vp_total"])) },
            { label: "Avg VP / vote", value: fmtNum(pickNumber(profile, ["avg_vp"])) },
            { label: "Participation rate", value: fmtPct(pickNumber(profile, ["participation_rate"])) },
            { label: "First vote", value: pickString(profile, ["first_vote_at"]).slice(0, 10) || "—" },
            { label: "Latest vote", value: pickString(profile, ["last_vote_at"]).slice(0, 10) || "—" },
          ]}
        />
      </DatasetPanel>

      <DatasetPanel
        title="Participation over time"
        descriptor={ctx.descriptors.voter_participation}
        groupLoaded
        hydrationPhase={ctx.hydrated.voter_participation?.phase}
        hydrationError={ctx.hydrated.voter_participation?.error}
      >
        <ChartCard
          chartId="gov-voter-participation"
          hideId
          sql={ctx.descriptors.voter_participation?.sql}
          sourceModel="governance_db"
          spec={participationSpec}
        />
      </DatasetPanel>

      <DatasetPanel title="Vote history" descriptor={votes} groupLoaded emptyLabel="No votes recorded.">
        <PaginatedTable
          dataset={votes}
          datasetKey="voter_votes"
          viewId={ctx.viewId}
          fetchRows={ctx.fetchRows}
          maxHeight="520px"
          hiddenColumns={hiddenColumnsFor("voter_votes")}
          columnLabels={COLUMN_LABELS}
          sourceLabel="Snapshot off-chain signaling"
          onCellClick={(column, _value, row) => {
            if (column !== "proposal_title") return;
            const id = row[voteIndex.get("proposal_id") ?? -1];
            if (id !== undefined && id !== null && id !== "") ctx.onEntity("proposal", String(id));
          }}
          renderCell={(column, value, row) => {
            if (column === "choice_kind") {
              // Per-row choice labels are resolved server-side for the single
              // case; ranked/unlabeled shapes fall back to index rendering.
              return (
                <VoteChoiceCell
                  kind={value}
                  index={row[voteIndex.get("choice_index") ?? -1]}
                  indexes={row[voteIndex.get("choice_indexes") ?? -1]}
                  label={row[voteIndex.get("choice_label") ?? -1]}
                  choices={[]}
                />
              );
            }
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
          datasetKey="voter_votes"
          descriptor={votes}
          fetchRows={ctx.fetchRows}
          scope={`voter_${identifier.slice(0, 10)}`}
          label="Export vote history CSV"
        />
        <AskCerebroButton state={ctx.state} aggregates={ctx.aggregates} sendMessage={ctx.sendMessage} />
      </div>
      <SignalingNote />
    </div>
  );
}
