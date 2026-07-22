import { useMemo } from "react";
import { ChartCard } from "../../../components/ChartCard";
import { AsyncButton } from "../../shared/AsyncButton";
import { FilterChips } from "../../shared/FilterChips";
import { MaField } from "../../shared/MaField";
import { MaToolbar } from "../../shared/MaToolbar";
import { PaginatedTable } from "../../shared/PaginatedTable";
import { SegmentedControl } from "../../shared/SegmentedControl";
import { DatasetPanel, GroupBanner } from "../components/DatasetPanel";
import { DateRangeControl } from "../components/DateRangeControl";
import { GipBadge } from "../components/GipBadge";
import { QuorumBadge } from "../components/QuorumBadge";
import { activityComboOption } from "../model/chartOptions";
import { COLUMN_LABELS, hiddenColumnsFor } from "../model/columns";
import { parseActivity } from "../model/parseRows";
import { EMPTY_DRAFT } from "../state/toolArgs";
import { shortAddr } from "../../../utils/format";
import { fmtNum, fmtPct, firstRow, GroupGate, KpiRow, pickNumber, useDataset, type GovViewContext } from "./common";

const PROPOSAL_TYPES = ["", "basic", "single-choice", "ranked-choice", "weighted", "quadratic", "approval"];
const SORTS: Array<{ value: string; label: string }> = [
  { value: "", label: "Default (newest)" },
  { value: "newest", label: "Newest" },
  { value: "oldest", label: "Oldest" },
  { value: "most_votes", label: "Most votes" },
  { value: "highest_participation", label: "Highest participation" },
  { value: "quorum_ratio", label: "Quorum ratio" },
  { value: "recently_ended", label: "Recently ended" },
];

/** Applied-filter echo chips: toggling a chip off clears that filter and
 * re-applies the section immediately. */
function AppliedFilters({ ctx }: { ctx: GovViewContext }) {
  const f = ctx.state.filters;
  const range = ctx.state.date_range;
  const options: Array<{ value: string; label: string }> = [];
  if (f.query) options.push({ value: "query", label: `text: ${f.query}` });
  if (f.proposal_state) options.push({ value: "proposal_state", label: `state: ${f.proposal_state}` });
  if (f.proposal_type) options.push({ value: "proposal_type", label: `type: ${f.proposal_type}` });
  if (f.quorum_status) options.push({ value: "quorum_status", label: `quorum: ${f.quorum_status}` });
  if (range.kind !== "all") {
    options.push({
      value: "range",
      label: range.kind === "relative" ? `last ${range.window_days === 365 ? "1y" : `${range.window_days}d`}` : "custom range",
    });
  }
  if (options.length === 0) return null;
  return (
    <div className="gov-applied-chips">
      <FilterChips
        label="Applied"
        options={options}
        selected={options.map((option) => option.value)}
        allowAllToggle={false}
        onChange={(next) => {
          const removed = options.map((option) => option.value).find((value) => !next.includes(value));
          if (!removed) return;
          const draft = { ...ctx.draft };
          if (removed === "query") draft.query = "";
          if (removed === "proposal_state") draft.proposal_state = "";
          if (removed === "proposal_type") draft.proposal_type = "";
          if (removed === "quorum_status") draft.quorum_status = "";
          if (removed === "range") {
            draft.days = 0;
            draft.start = "";
            draft.end = "";
          }
          ctx.setDraft(draft);
          ctx.apply("proposals", draft);
        }}
      />
    </div>
  );
}

export function ProposalsSection({ ctx }: { ctx: GovViewContext }) {
  const groups = ctx.state.loaded_groups ?? {};
  const summary = firstRow(ctx, "proposal_summary");
  const proposals = ctx.descriptors.proposals;
  const colIndex = new Map((proposals?.columns ?? []).map((column, index) => [column.name, index]));
  const cell = (row: unknown[], name: string) => row[colIndex.get(name) ?? -1];
  const activityDs = useDataset(ctx, "proposal_activity");
  const activitySpec = useMemo(() => activityComboOption(parseActivity(activityDs), [
    { field: "proposals_started", label: "Proposals started", type: "bar" },
    { field: "votes_cast", label: "Votes cast", type: "line", yAxisIndex: 1 },
  ], "votes"), [activityDs]);

  return (
    <>
      <MaToolbar className="gov-toolbar">
        <MaField className="gov-query">
          <input
            type="text"
            aria-label="Proposal text filter"
            placeholder="Filter title text…"
            value={ctx.draft.query}
            onChange={(event) => ctx.setDraft((draft) => ({ ...draft, query: event.target.value }))}
          />
        </MaField>
        <SegmentedControl<string>
          ariaLabel="Proposal state"
          size="sm"
          value={ctx.draft.proposal_state || "all"}
          options={[
            { value: "all", label: "All" },
            { value: "active", label: "Active" },
            { value: "pending", label: "Pending" },
            { value: "closed", label: "Closed" },
          ]}
          onChange={(next) => ctx.setDraft((draft) => ({ ...draft, proposal_state: next === "all" ? "" : next }))}
        />
        <label>
          Type
          <select
            value={ctx.draft.proposal_type}
            onChange={(event) => ctx.setDraft((draft) => ({ ...draft, proposal_type: event.target.value }))}
          >
            {PROPOSAL_TYPES.map((type) => <option key={type} value={type}>{type || "All"}</option>)}
          </select>
        </label>
        <SegmentedControl<string>
          ariaLabel="Quorum status"
          size="sm"
          value={ctx.draft.quorum_status || "all"}
          options={[
            { value: "all", label: "All" },
            { value: "met", label: "Met" },
            { value: "missed", label: "Missed" },
            { value: "unspecified", label: "Unspecified" },
          ]}
          onChange={(next) => ctx.setDraft((draft) => ({ ...draft, quorum_status: next === "all" ? "" : next }))}
        />
        <DateRangeControl draft={ctx.draft} onChange={(next) => ctx.setDraft(next)} />
        <label>
          Sort
          <select
            value={ctx.draft.sort_by}
            onChange={(event) => ctx.setDraft((draft) => ({ ...draft, sort_by: event.target.value }))}
          >
            {SORTS.map((sort) => <option key={sort.value} value={sort.value}>{sort.label}</option>)}
          </select>
        </label>
        <AsyncButton loadingLabel="Applying" disabled={ctx.loading} onClick={() => ctx.apply("proposals")}>
          Apply
        </AsyncButton>
        <AsyncButton
          variant="ghost"
          loadingLabel="Resetting"
          onClick={() => {
            ctx.setDraft(EMPTY_DRAFT);
            ctx.apply("proposals", EMPTY_DRAFT);
          }}
        >
          Reset
        </AsyncButton>
      </MaToolbar>
      <AppliedFilters ctx={ctx} />

      <GroupGate ctx={ctx} section="proposals" group="core">
        {summary && (
          <KpiRow
            items={[
              { label: "Proposals (scope)", value: fmtNum(pickNumber(summary, ["proposal_count", "proposals"])) },
              { label: "Votes", value: fmtNum(pickNumber(summary, ["vote_count", "votes"])) },
              { label: "Unique voters", value: fmtNum(pickNumber(summary, ["voter_count", "unique_voters"])) },
              { label: "Quorum met", value: fmtNum(pickNumber(summary, ["quorum_met_count", "quorum_met"])) },
              { label: "Median votes / proposal", value: fmtNum(pickNumber(summary, ["median_votes", "median_votes_per_proposal"])) },
            ]}
          />
        )}
        <DatasetPanel
          title="Proposals"
          descriptor={proposals}
          groupLoaded={groups["proposals.core"]}
          onRetry={() => ctx.retryGroup("proposals", "core")}
          emptyLabel="No proposals match the applied filters."
        >
          <PaginatedTable
            dataset={proposals}
            datasetKey="proposals"
            viewId={ctx.viewId}
            fetchRows={ctx.fetchRows}
            maxHeight="560px"
            hiddenColumns={hiddenColumnsFor("proposals")}
            columnLabels={COLUMN_LABELS}
            sourceLabel="Snapshot off-chain signaling"
            onCellClick={(column, _value, row) => {
              if (column !== "title") return;
              const id = String(cell(row, "id") ?? "");
              if (id) ctx.onEntity("proposal", id);
            }}
            renderCell={(column, value, row) => {
              if (column === "title") {
                return (
                  <span>
                    {String(value ?? "")}
                    <GipBadge gip={pickNumber({ gip: cell(row, "gip_number") }, ["gip"])} />
                  </span>
                );
              }
              if (column === "state") {
                const state = String(value ?? "");
                return <span className={`gov-state-chip gov-state-chip--${state}`}>{state || "—"}</span>;
              }
              if (column === "author") {
                return <span className="gov-mono" title={String(value ?? "")}>{shortAddr(String(value ?? ""))}</span>;
              }
              if (column === "quorum_status") {
                return (
                  <QuorumBadge
                    scoresTotal={pickNumber({ v: cell(row, "scores_total") }, ["v"])}
                    quorum={pickNumber({ v: cell(row, "quorum") }, ["v"])}
                  />
                );
              }
              if (column === "quorum_ratio" || column === "leading_choice_share") {
                return <span>{value === null || value === "" ? "—" : fmtPct(value)}</span>;
              }
              if (column === "start_at" || column === "end_at") {
                return <span className="gov-mono">{String(value ?? "").slice(0, 10)}</span>;
              }
              return undefined;
            }}
          />
        </DatasetPanel>
      </GroupGate>

      <GroupGate ctx={ctx} section="proposals" group="charts">
        <GroupBanner groupLoaded={groups["proposals.charts"]} onRetry={() => ctx.retryGroup("proposals", "charts")} />
        <DatasetPanel
          title="Proposal activity"
          descriptor={ctx.descriptors.proposal_activity}
          groupLoaded={groups["proposals.charts"]}
          hydrationPhase={ctx.hydrated.proposal_activity?.phase}
          hydrationError={ctx.hydrated.proposal_activity?.error}
          onRetry={() => ctx.retryGroup("proposals", "charts")}
        >
          <ChartCard
            chartId="gov-proposal-activity"
            hideId
            sql={ctx.descriptors.proposal_activity?.sql}
            sourceModel="governance_db"
            spec={activitySpec}
          />
        </DatasetPanel>
      </GroupGate>
    </>
  );
}
