// One pool. Tabs partition the `pool` section's groups (model/navGroups.ts):
// a reserves-only (Balancer) pool exposes Reserves + Publication only; a
// state-only CL pool (`ticks_probed = 0`) keeps every tab but renders a stub
// where the probe-dependent datasets are empty — never an empty chart that
// looks like "no liquidity".

import { useMemo, useState } from "react";

import { ChartCard } from "../../../components/ChartCard";
import { useTheme } from "../../../hooks/useTheme";
import { MaSkeletonKpiGrid, MaSkeletonRows } from "../../shared/MiniAppChrome";
import { TabBar } from "../../shared/TabBar";
import { DatasetPanel } from "../components/DatasetPanel";
import { DatasetInfo } from "../components/InfoPopover";
import { LiquidityProfilePanel } from "../components/LiquidityProfilePanel";
import { PlxTable } from "../components/PlxTable";
import { PoolStateHeader } from "../components/PoolStateHeader";
import { ProfileControls } from "../components/ProfileControls";
import { ProfileDatePicker } from "../components/ProfileDatePicker";
import { ProfileHeatmapPanel } from "../components/ProfileHeatmapPanel";
import {
  feesHistoryOption, liquidityHistoryOption, priceHistoryOption, reservesHistoryOption, ticksOption,
} from "../model/chartOptions";
import { fmtDate, fmtInt, tokenLabel } from "../model/format";
import { unitLabel, type AxisLabelContext } from "../model/liquidityProfile";
import { POOL_TABS, coercePoolTab, tabsForPool, type PoolTabId } from "../model/navGroups";
import {
  parseFeeGrowth, parsePoolDetail, parseProfileRanges, parsePublicationFacts, parseReservesHistory,
  parseStateHistory, parseTicks, withDerivedFeeUnits, type PoolDetailView,
} from "../model/parseRows";
import { useDataset, type PlxViewContext } from "../sections/common";

const SRC = "rpc_state_indexer";

function StateOnlyStub({ detail, what }: { detail: PoolDetailView; what: string }) {
  return (
    <div className="plx-stateonly" role="status">
      <strong>State only — {what} not available.</strong>
      <span>
        This pool sat below the active-liquidity threshold at its publication (<code>cl_below_active_threshold</code>),
        so the indexer recorded slot0 state (tick, price, liquidity{detail.liquidity !== null ? ` = ${fmtInt(detail.liquidity)}` : ""}) but did not probe its
        initialized ticks. Profile, tick and fee datasets are empty by construction, not by failure.
        {detail.profileAvailableFrom ? ` A profile exists for earlier dates from ${fmtDate(detail.profileAvailableFrom)} — pick one in the Profile tab.` : ""}
      </span>
    </div>
  );
}

function historyRetry(ctx: PlxViewContext, group: string) {
  return () => ctx.retryGroup("pool", group);
}

export function PoolDetail({ ctx }: { ctx: PlxViewContext }) {
  const { isDark } = useTheme();
  const groups = ctx.state.loaded_groups ?? {};
  const detailDs = useDataset(ctx, "pool_detail");
  const detail = useMemo(() => parsePoolDetail(detailDs), [detailDs]);
  const [yLog, setYLog] = useState(false);

  const stateDs = useDataset(ctx, "pool_state_history");
  const statePoints = useMemo(() => parseStateHistory(stateDs), [stateDs]);
  const reservesDs = useDataset(ctx, "pool_reserves_history");
  const reserveSeries = useMemo(() => parseReservesHistory(reservesDs), [reservesDs]);
  const feesDs = useDataset(ctx, "pool_fee_growth");
  const feePoints = useMemo(
    () => withDerivedFeeUnits(parseFeeGrowth(feesDs), detail?.token0Decimals ?? null, detail?.token1Decimals ?? null),
    [feesDs, detail?.token0Decimals, detail?.token1Decimals],
  );
  const ticksDs = useDataset(ctx, "pool_ticks_at");
  const tickPoints = useMemo(() => parseTicks(ticksDs), [ticksDs]);
  const profileDs = useDataset(ctx, "pool_profile_at");
  const profileParse = useMemo(() => parseProfileRanges(profileDs), [profileDs]);
  const factsDs = useDataset(ctx, "pool_publication_facts");
  const facts = useMemo(() => parsePublicationFacts(factsDs), [factsDs]);

  const client = ctx.client;
  const family = detail?.poolFamily ?? null;
  const tab: PoolTabId = coercePoolTab(client.tab, family);
  const tabs = tabsForPool(family);
  const rawPrice = !detail || detail.token0Decimals === null || detail.token1Decimals === null;
  const sym0 = detail ? tokenLabel(detail.token0Symbol, detail.token0) : "token0";
  const sym1 = detail ? tokenLabel(detail.token1Symbol, detail.token1) : "token1";
  const labelCtx: AxisLabelContext = {
    mode: client.axis,
    currentTick: detail?.currentTick ?? null,
    dec0: detail?.token0Decimals ?? null,
    dec1: detail?.token1Decimals ?? null,
    inverted: client.inverted,
  };
  const priceUnit = unitLabel({
    mode: "price", sym0, sym1, dec0: detail?.token0Decimals ?? null, dec1: detail?.token1Decimals ?? null, inverted: client.inverted,
  });

  const priceSpec = useMemo(
    () => priceHistoryOption(statePoints, { raw: rawPrice, unit: priceUnit, inverted: client.inverted }),
    [statePoints, rawPrice, priceUnit, client.inverted],
  );
  const liquiditySpec = useMemo(() => liquidityHistoryOption(statePoints), [statePoints]);
  const reservesSpec = useMemo(() => reservesHistoryOption(reserveSeries), [reserveSeries]);
  const feesSpec = useMemo(
    () => feesHistoryOption(feePoints, {
      sym0, sym1,
      units0: feePoints.some((point) => point.fees0Units !== null),
      units1: feePoints.some((point) => point.fees1Units !== null),
    }),
    [feePoints, sym0, sym1],
  );
  const ticksSpec = useMemo(
    () => ticksOption(tickPoints, labelCtx, isDark),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tickPoints, client.axis, client.inverted, detail?.currentTick, detail?.token0Decimals, detail?.token1Decimals, isDark],
  );

  if (!detail) {
    const coreLoaded = groups["pool.core"];
    if (coreLoaded === false || (coreLoaded === undefined && !detailDs)) {
      return (
        <div className="plx-skel" aria-busy="true" aria-label="Loading pool">
          <MaSkeletonKpiGrid />
          <MaSkeletonRows count={6} />
        </div>
      );
    }
    return (
      <DatasetPanel
        title="Pool"
        descriptor={ctx.descriptors.pool_detail}
        groupLoaded={coreLoaded}
        onRetry={() => ctx.retryGroup("pool", "core")}
        emptyLabel={(
          <span>
            <strong>{ctx.state.selected_entity?.identifier ?? "This address"}</strong> is not a configured pool in the indexer —
            neither job (daily_cl_liquidity, daily_pool_reserves) tracks it.
          </span>
        )}
      >
        <span />
      </DatasetPanel>
    );
  }

  const cl = detail.poolFamily !== "reserves_only";
  const needsProbe = POOL_TABS.find((entry) => entry.id === tab)?.needsProbe ?? false;
  const stateOnly = cl && !detail.probed && needsProbe;
  const overTimeAvailable = cl && detail.probed;

  return (
    <div className="plx-entity">
      <PoolStateHeader detail={detail} inverted={client.inverted} overlay={ctx.overlay} onToken={(address) => ctx.onEntity("token", address)} />
      <div className="plx-subtabs">
        <TabBar<PoolTabId>
          ariaLabel="Pool views"
          tabs={tabs.map((entry) => ({ id: entry.id, label: entry.label }))}
          active={tab}
          onChange={(next) => ctx.setClient({ tab: next })}
          scrollOnChange={false}
        />
      </div>

      {tab === "profile" && (
        <>
          <ProfileControls
            client={client}
            onChange={(patch) => ctx.setClient(patch)}
            yLog={yLog}
            onYLog={setYLog}
            overTimeAvailable={overTimeAvailable}
          />
          {stateOnly && profileParse.ranges.length === 0 ? (
            <>
              <ProfileDatePicker
                applied={profileParse.asOf}
                requested={ctx.state.as_of ?? ""}
                latest={detail.asOf}
                earliest={detail.profileAvailableFrom}
                onChange={ctx.onLoadProfileDate}
              />
              <StateOnlyStub detail={detail} what="liquidity profile" />
            </>
          ) : client.view === "over_time" && overTimeAvailable ? (
            <ProfileHeatmapPanel ctx={ctx} detail={detail} />
          ) : (
            <>
              <ProfileDatePicker
                applied={profileParse.asOf}
                requested={ctx.state.as_of ?? ""}
                latest={detail.asOf}
                earliest={detail.profileAvailableFrom}
                onChange={ctx.onLoadProfileDate}
              />
              <LiquidityProfilePanel ctx={ctx} detail={detail} yLog={yLog} />
            </>
          )}
        </>
      )}

      {tab === "history" && (
        <>
          <div className="plx-grid-2">
            <DatasetPanel
              title={`Price (${priceUnit})`}
              descriptor={ctx.descriptors.pool_state_history}
              groupLoaded={groups["pool.history"]}
              hydrationPhase={ctx.hydrated.pool_state_history?.phase}
              hydrationError={ctx.hydrated.pool_state_history?.error}
              onRetry={historyRetry(ctx, "history")}
              meta={<DatasetInfo datasetKey="pool_state_history" descriptor={ctx.descriptors.pool_state_history} />}
            >
              <ChartCard chartId="plx-price" hideId sql={ctx.descriptors.pool_state_history?.sql} sourceModel={SRC} spec={priceSpec} />
              {rawPrice && <div className="plx-hint plx-warn">Raw units — token decimals are not resolved for this pair.</div>}
            </DatasetPanel>
            <DatasetPanel
              title="Liquidity and initialized ticks"
              descriptor={ctx.descriptors.pool_state_history}
              groupLoaded={groups["pool.history"]}
              hydrationPhase={ctx.hydrated.pool_state_history?.phase}
              hydrationError={ctx.hydrated.pool_state_history?.error}
              onRetry={historyRetry(ctx, "history")}
            >
              <ChartCard chartId="plx-liquidity" hideId spec={liquiditySpec} />
            </DatasetPanel>
          </div>
          <DatasetPanel
            title="Daily state"
            descriptor={ctx.descriptors.pool_state_history}
            groupLoaded={groups["pool.history"]}
            onRetry={historyRetry(ctx, "history")}
          >
            <PlxTable datasetKey="pool_state_history" descriptor={ctx.descriptors.pool_state_history} viewId={ctx.viewId} fetchRows={ctx.fetchRows} overlay={ctx.overlay} maxHeight="360px" />
          </DatasetPanel>
        </>
      )}

      {tab === "reserves" && (
        <>
          <DatasetPanel
            title="Raw reserves over time"
            descriptor={ctx.descriptors.pool_reserves_history}
            groupLoaded={groups["pool.history"]}
            hydrationPhase={ctx.hydrated.pool_reserves_history?.phase}
            hydrationError={ctx.hydrated.pool_reserves_history?.error}
            onRetry={historyRetry(ctx, "history")}
            meta={<DatasetInfo datasetKey="pool_reserves_history" descriptor={ctx.descriptors.pool_reserves_history} />}
          >
            <ChartCard chartId="plx-reserves" hideId sql={ctx.descriptors.pool_reserves_history?.sql} sourceModel={SRC} spec={reservesSpec} />
            <div className="plx-hint">One axis per token. Units where decimals are known, raw base units otherwise — never USD.</div>
          </DatasetPanel>
          <DatasetPanel
            title="Daily balances"
            descriptor={ctx.descriptors.pool_reserves_history}
            groupLoaded={groups["pool.history"]}
            onRetry={historyRetry(ctx, "history")}
          >
            <PlxTable datasetKey="pool_reserves_history" descriptor={ctx.descriptors.pool_reserves_history} viewId={ctx.viewId} fetchRows={ctx.fetchRows} overlay={ctx.overlay} maxHeight="360px" />
          </DatasetPanel>
        </>
      )}

      {tab === "fees" && (
        stateOnly ? (
          <StateOnlyStub detail={detail} what="fee estimates" />
        ) : (
          <>
            <DatasetPanel
              title="Estimated fees per day"
              descriptor={ctx.descriptors.pool_fee_growth}
              groupLoaded={groups["pool.fees"]}
              hydrationPhase={ctx.hydrated.pool_fee_growth?.phase}
              hydrationError={ctx.hydrated.pool_fee_growth?.error}
              onRetry={historyRetry(ctx, "fees")}
              meta={<DatasetInfo datasetKey="pool_fee_growth" descriptor={ctx.descriptors.pool_fee_growth} />}
              emptyLabel="No fee-growth history for this pool in the window."
            >
              <ChartCard chartId="plx-fees" hideId sql={ctx.descriptors.pool_fee_growth?.sql} sourceModel={SRC} spec={feesSpec} />
              <div className="plx-hint">Δ fee growth × liquidity / 2^128 between consecutive publications. Null on the first row, on a negative delta and at zero liquidity — an estimate, not a ledger.</div>
            </DatasetPanel>
            <DatasetPanel
              title="Fee growth rows"
              descriptor={ctx.descriptors.pool_fee_growth}
              groupLoaded={groups["pool.fees"]}
              onRetry={historyRetry(ctx, "fees")}
            >
              <PlxTable datasetKey="pool_fee_growth" descriptor={ctx.descriptors.pool_fee_growth} viewId={ctx.viewId} fetchRows={ctx.fetchRows} overlay={ctx.overlay} maxHeight="360px" />
            </DatasetPanel>
          </>
        )
      )}

      {tab === "ticks" && (
        stateOnly ? (
          <StateOnlyStub detail={detail} what="initialized ticks" />
        ) : (
          <>
            <DatasetPanel
              title="Initialized ticks"
              descriptor={ctx.descriptors.pool_ticks_at}
              groupLoaded={groups["pool.profile"]}
              hydrationPhase={ctx.hydrated.pool_ticks_at?.phase}
              hydrationError={ctx.hydrated.pool_ticks_at?.error}
              onRetry={historyRetry(ctx, "profile")}
              meta={<DatasetInfo datasetKey="pool_ticks_at" descriptor={ctx.descriptors.pool_ticks_at} />}
              emptyLabel="No initialized ticks published at this date."
            >
              <ChartCard chartId="plx-ticks" hideId sql={ctx.descriptors.pool_ticks_at?.sql} sourceModel={SRC} spec={ticksSpec} renderer="svg" />
              <div className="plx-hint">Net liquidity is what crossing the tick adds (or removes, negative); gross is the total referenced by positions at that tick.</div>
            </DatasetPanel>
            <DatasetPanel
              title="Tick rows"
              descriptor={ctx.descriptors.pool_ticks_at}
              groupLoaded={groups["pool.profile"]}
              onRetry={historyRetry(ctx, "profile")}
            >
              <PlxTable datasetKey="pool_ticks_at" descriptor={ctx.descriptors.pool_ticks_at} viewId={ctx.viewId} fetchRows={ctx.fetchRows} overlay={ctx.overlay} maxHeight="360px" />
            </DatasetPanel>
          </>
        )
      )}

      {tab === "publication" && (
        <DatasetPanel
          title="Publication facts"
          descriptor={ctx.descriptors.pool_publication_facts}
          groupLoaded={groups["pool.core"]}
          onRetry={() => ctx.retryGroup("pool", "core")}
          meta={<DatasetInfo datasetKey="pool_publication_facts" descriptor={ctx.descriptors.pool_publication_facts} />}
          emptyLabel="No publication row at this date."
        >
          <div className="plx-strip">
            {facts.map((fact) => (
              <span key={`${fact.job}-${fact.date}`}>
                <strong>{fact.job}</strong> {fmtDate(fact.date)} · block {fmtInt(fact.anchorBlock)}
                {fact.observationsTotal === null ? "" : ` · ${fmtInt(fact.observationsTotal)} observations`}
                {fact.executorKind ? ` · ${fact.executorKind}` : ""}
                {fact.job === "daily_cl_liquidity" && (fact.probed ? " · probed" : " · state only")}
              </span>
            ))}
          </div>
          <PlxTable datasetKey="pool_publication_facts" descriptor={ctx.descriptors.pool_publication_facts} viewId={ctx.viewId} fetchRows={ctx.fetchRows} overlay={ctx.overlay} maxHeight="320px" />
        </DatasetPanel>
      )}
    </div>
  );
}
