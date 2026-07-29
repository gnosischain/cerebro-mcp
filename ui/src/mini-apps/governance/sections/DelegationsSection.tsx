import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import { ChartCard } from "../../../components/ChartCard";
import { shortAddr } from "../../../utils/format";
import { PaginatedTable } from "../../shared/PaginatedTable";
import { finite, rowsToObjects } from "../../shared/rowDataset";
import { DatasetPanel, GroupBanner } from "../components/DatasetPanel";
import { activityComboOption, concentrationOption, horizontalBarOption } from "../model/chartOptions";
import { COLUMN_LABELS, hiddenColumnsFor } from "../model/columns";
import { parseActivity } from "../model/parseRows";
import { fmtNum, fmtPct, firstRow, GroupGate, KpiRow, pickNumber, useDataset, type GovViewContext } from "./common";

// Delegations: Snapshot DelegateRegistry (on-chain) via the rpc_log_indexer
// view, which carries BOTH Ethereum mainnet (chain 1) and Gnosis Chain
// (chain 100) — the gnosis.eth space delegates on both, and a delegator can
// delegate independently on each.
//
// Edge counts (delegators/delegates/churn) come straight from the registry.
// "Delegated voting power" is Snapshot's realized vp_by_strategy delegation
// share, which exists ONLY for delegates that have voted — the server returns
// NULL, never 0, where no realized figure exists. Both lenses are labelled
// distinctly: counts are never voting power.

const DELEGATE_SRC = "rpc_log_indexer";
const POWER_SRC = "governance_db + rpc_log_indexer";
const POWER_BARS = 12;

export function DelegationsSection({ ctx }: { ctx: GovViewContext }) {
  const groups = ctx.state.loaded_groups ?? {};
  const summary = firstRow(ctx, "delegation_summary");
  const leaderboard = ctx.descriptors.top_delegates;
  const retryInsights = () => ctx.retryGroup("delegations", "insights");

  const activityDs = useDataset(ctx, "delegation_activity");
  const activitySpec = useMemo(() => activityComboOption(parseActivity(activityDs), [
    { field: "set_events", label: "Set", type: "bar" },
    { field: "clear_events", label: "Clear", type: "bar" },
    { field: "cumulative_net", label: "Cumulative net", type: "line", yAxisIndex: 1 },
  ], "net active"), [activityDs]);

  const churnDs = useDataset(ctx, "delegation_churn");
  const churnSpec = useMemo(() => activityComboOption(parseActivity(churnDs), [
    { field: "new_delegators", label: "New", type: "bar" },
    { field: "repointed", label: "Re-pointed", type: "bar" },
    { field: "cleared", label: "Cleared", type: "bar" },
  ]), [churnDs]);

  const powerDs = useDataset(ctx, "delegation_power");
  // A delegate who has never voted has NO realized vp_by_strategy, so the
  // server sends NULL. finite() maps that to null and the bar chart cannot draw
  // it — but dropping those rows silently would make the survivors read as the
  // whole delegate set, so the count is carried into the caption.
  const powerRows = useMemo(() => rowsToObjects(powerDs), [powerDs]);
  const unmeasured = useMemo(
    () => powerRows.filter((row) => finite(row.delegated_vp_total) === null).length,
    [powerRows],
  );
  const powerSpec = useMemo(() => {
    const spec = horizontalBarOption(
      powerRows
        .flatMap((row) => {
          const value = finite(row.delegated_vp_total);
          return value === null ? [] : [{ name: shortAddr(String(row.delegate ?? "")), value }];
        })
        .filter((row) => row.value > 0)
        .slice(0, POWER_BARS),
      "Delegated VP",
    ) as EChartsOption & { _cerebro_height?: string };
    // Use the shared 350px card height so this chart aligns with its siblings
    // in the grid row (horizontalBarOption otherwise sizes to row count).
    delete spec._cerebro_height;
    return spec;
  }, [powerRows]);

  const concentrationDs = useDataset(ctx, "delegation_concentration");
  const concentrationSpec = useMemo(() => concentrationOption(
    rowsToObjects(concentrationDs).flatMap((row) => {
      const tier = finite(row.tier);
      return tier === null ? [] : [{ tier, share: finite(row.share) }];
    }),
    "Delegator share",
  ), [concentrationDs]);

  return (
    <>
      <GroupGate ctx={ctx} section="delegations" group="core">
        {summary && (
          <KpiRow
            items={[
              { label: "Active delegators", value: fmtNum(pickNumber(summary, ["active_delegators"])) },
              { label: "Active delegates", value: fmtNum(pickNumber(summary, ["active_delegates"])) },
              { label: "Delegation events", value: fmtNum(pickNumber(summary, ["total_events"])) },
              { label: "Re-delegations", value: fmtNum(pickNumber(summary, ["re_delegations"])) },
              { label: "Clear rate", value: fmtPct(pickNumber(summary, ["clear_rate"])) },
            ]}
          />
        )}
        <DatasetPanel
          title="Top delegates"
          descriptor={leaderboard}
          groupLoaded={groups["delegations.core"]}
          onRetry={() => ctx.retryGroup("delegations", "core")}
          emptyLabel="No active delegations found."
        >
          <PaginatedTable
            dataset={leaderboard}
            datasetKey="top_delegates"
            viewId={ctx.viewId}
            fetchRows={ctx.fetchRows}
            maxHeight="520px"
            hiddenColumns={hiddenColumnsFor("top_delegates")}
            columnLabels={COLUMN_LABELS}
            sourceLabel="Snapshot delegate registry (Ethereum mainnet + Gnosis Chain)"
            renderCell={(column, value) => {
              if (column === "delegate") {
                return <span className="gov-mono" title={String(value ?? "")}>{shortAddr(String(value ?? ""))}</span>;
              }
              if (column === "first_delegation_at" || column === "last_delegation_at") {
                return <span className="gov-mono">{String(value ?? "").slice(0, 10)}</span>;
              }
              return undefined;
            }}
          />
        </DatasetPanel>
        <p className="gov-caption">
          Delegation edges from the Snapshot DelegateRegistry on Ethereum mainnet and Gnosis
          Chain — both count toward the gnosis.eth space, and a delegator can delegate
          independently on each chain.
        </p>
      </GroupGate>

      <GroupGate ctx={ctx} section="delegations" group="insights">
        <GroupBanner groupLoaded={groups["delegations.insights"]} onRetry={retryInsights} />
        <div className="gov-grid-2">
          <DatasetPanel
            title="Delegation activity"
            descriptor={ctx.descriptors.delegation_activity}
            groupLoaded={groups["delegations.insights"]}
            hydrationPhase={ctx.hydrated.delegation_activity?.phase}
            hydrationError={ctx.hydrated.delegation_activity?.error}
            onRetry={retryInsights}
          >
            <ChartCard
              chartId="gov-delegation-activity"
              hideId
              sql={ctx.descriptors.delegation_activity?.sql}
              sourceModel={DELEGATE_SRC}
              spec={activitySpec}
            />
          </DatasetPanel>
          <DatasetPanel
            title="Delegated voting power (top delegates)"
            descriptor={ctx.descriptors.delegation_power}
            groupLoaded={groups["delegations.insights"]}
            hydrationPhase={ctx.hydrated.delegation_power?.phase}
            hydrationError={ctx.hydrated.delegation_power?.error}
            onRetry={retryInsights}
          >
            <ChartCard
              chartId="gov-delegation-power"
              hideId
              sql={ctx.descriptors.delegation_power?.sql}
              sourceModel={POWER_SRC}
              spec={powerSpec}
            />
          </DatasetPanel>
        </div>
        <div className="gov-grid-2">
          <DatasetPanel
            title="Delegation concentration"
            descriptor={ctx.descriptors.delegation_concentration}
            groupLoaded={groups["delegations.insights"]}
            hydrationPhase={ctx.hydrated.delegation_concentration?.phase}
            hydrationError={ctx.hydrated.delegation_concentration?.error}
            onRetry={retryInsights}
          >
            <ChartCard
              chartId="gov-delegation-concentration"
              hideId
              sql={ctx.descriptors.delegation_concentration?.sql}
              sourceModel={DELEGATE_SRC}
              spec={concentrationSpec}
            />
          </DatasetPanel>
          <DatasetPanel
            title="Delegation churn"
            descriptor={ctx.descriptors.delegation_churn}
            groupLoaded={groups["delegations.insights"]}
            hydrationPhase={ctx.hydrated.delegation_churn?.phase}
            hydrationError={ctx.hydrated.delegation_churn?.error}
            onRetry={retryInsights}
          >
            <ChartCard
              chartId="gov-delegation-churn"
              hideId
              sql={ctx.descriptors.delegation_churn?.sql}
              sourceModel={DELEGATE_SRC}
              spec={churnSpec}
            />
          </DatasetPanel>
        </div>
        <p className="gov-caption">
          Delegated voting power is Snapshot&apos;s realized vp_by_strategy at each delegate&apos;s
          latest final vote, measured at snapshot time (not live). The delegation strategies are
          resolved by name and network from <em>that proposal&apos;s own</em> strategy list:
          gnosis.eth has rewritten it three times and the chain order is not the same across
          them, so a fixed position would read the wrong chain — or nothing at all.
          {unmeasured > 0 && (
            <> {unmeasured} of {powerRows.length} delegates have never voted, so no realized
            figure exists for them — that is unknown, not zero, and they are absent from this
            chart rather than sitting at the bottom of it.</>
          )}{" "}
          Concentration is by delegator headcount, not voting power.
        </p>
      </GroupGate>
    </>
  );
}
