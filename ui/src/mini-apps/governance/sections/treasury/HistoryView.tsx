import { useMemo } from "react";

import { ChartCard } from "../../../../components/ChartCard";
import { DatasetPanel, GroupBanner } from "../../components/DatasetPanel";
import { chainName } from "../../components/TreasuryChainCard";
import {
  breadthOption,
  constantPriceStackOption,
  timeSeriesLineOption,
} from "../../model/chartOptions";
import { chainHistory, tokenSeries } from "../../model/treasuryHistory";
import { priceFor, type PriceSource } from "../../model/treasuryPricing";
import { GroupGate, type GovViewContext } from "../common";
import type { TreasuryModel } from "./shared";

// "How it got here." One block per chain, never overlaid — Ethereum's series
// runs to 2026-07 and Gnosis Chain's stops in 2022-12, so a shared axis would
// read as a collapse rather than as two independently-indexed planes.
//
// Every history panel is unit-denominated except the revaluation stack, which
// is units x TODAY's price and says so in its own caption.

const HISTORY_SRC = "rpc_state_indexer.v_treasury_balances (monthly)";

export function HistoryView({ ctx, model }: { ctx: GovViewContext; model: TreasuryModel }) {
  const groups = ctx.state.loaded_groups ?? {};
  const onRetry = () => ctx.retryGroup("treasury", "history");

  return (
    <GroupGate ctx={ctx} section="treasury" group="history">
      <GroupBanner groupLoaded={groups["treasury.history"]} onRetry={onRetry} />
      {model.historyChains.map((chainId) => (
        <ChainHistory
          key={chainId}
          ctx={ctx}
          chainId={chainId}
          chainHistRows={model.chainHistRows}
          tokenHistRows={model.tokenHistRows}
          priceSource={model.priceSource}
          groupLoaded={groups["treasury.history"]}
          onRetry={onRetry}
        />
      ))}
    </GroupGate>
  );
}

/** One chain's history. Its own component so each chain gets its own hooks and
 * its own axes — two chains never share a chart. */
function ChainHistory({
  ctx,
  chainId,
  chainHistRows,
  tokenHistRows,
  priceSource,
  groupLoaded,
  onRetry,
}: {
  ctx: GovViewContext;
  chainId: number;
  chainHistRows: Array<Record<string, unknown>>;
  tokenHistRows: Array<Record<string, unknown>>;
  priceSource: PriceSource | null;
  groupLoaded: boolean | "partial" | undefined;
  onRetry: () => void;
}) {
  const points = useMemo(() => chainHistory(chainHistRows, chainId), [chainHistRows, chainId]);
  const rows = useMemo(
    () => points.map((point) => ({
      bucket: point.bucket,
      gno_units: point.gnoUnits,
      gno_units_ex_ltd: point.gnoUnitsExLtd,
      tokens_held: point.tokensHeld,
      tokens_named: point.tokensNamed,
      positions: point.positions,
    })),
    [points],
  );

  const gnoSpec = useMemo(
    () => timeSeriesLineOption(rows, {
      xField: "bucket",
      unitLabel: "GNO",
      series: [
        { field: "gno_units", label: "GNO" },
        { field: "gno_units_ex_ltd", label: "ex-Ltd.", dashed: true },
      ],
    }),
    [rows],
  );

  const breadthSpec = useMemo(
    () => breadthOption(rows, {
      xField: "bucket",
      namedField: "tokens_named",
      heldField: "tokens_held",
      positionsField: "positions",
    }),
    [rows],
  );

  const priceOf = useMemo(
    () => (token: string) => priceFor(priceSource, chainId, token),
    [priceSource, chainId],
  );
  const tokens = useMemo(
    () => tokenSeries(tokenHistRows, chainId, priceOf),
    [tokenHistRows, chainId, priceOf],
  );
  const stackSpec = useMemo(() => constantPriceStackOption(tokens.series, {}), [tokens.series]);

  const label = chainName(chainId);
  if (points.length === 0) return null;

  return (
    <>
      <div className="gov-grid-2">
        <DatasetPanel
          title={`${label} — GNO over time`}
          descriptor={ctx.descriptors.treasury_chain_history}
          groupLoaded={groupLoaded}
          onRetry={onRetry}
        >
          <ChartCard
            chartId={`gov-treasury-gno-${chainId}`}
            hideId
            sql={ctx.descriptors.treasury_chain_history?.sql}
            sourceModel={HISTORY_SRC}
            spec={gnoSpec}
          />
        </DatasetPanel>
        <DatasetPanel
          title={`${label} — portfolio breadth`}
          descriptor={ctx.descriptors.treasury_chain_history}
          groupLoaded={groupLoaded}
          onRetry={onRetry}
        >
          <ChartCard
            chartId={`gov-treasury-breadth-${chainId}`}
            hideId
            sql={ctx.descriptors.treasury_chain_history?.sql}
            sourceModel={HISTORY_SRC}
            spec={breadthSpec}
          />
        </DatasetPanel>
      </div>
      <p className="gov-caption">
        Named counts move between snapshots because token metadata resolves per anchor — a drop is
        the indexer learning less at that block, not the DAO disposing of tokens.
      </p>

      <DatasetPanel
        title={`${label} — holdings revalued at today's price`}
        descriptor={ctx.descriptors.treasury_token_history}
        groupLoaded={groupLoaded}
        onRetry={onRetry}
      >
        <ChartCard
          chartId={`gov-treasury-stack-${chainId}`}
          hideId
          sql={ctx.descriptors.treasury_token_history?.sql}
          sourceModel={HISTORY_SRC}
          spec={stackSpec}
        />
        <p className="gov-caption">
          Past balances are valued at <strong>today&apos;s</strong> spot price — a constant-price
          revaluation showing how the portfolio&apos;s shape changed, <strong>not</strong> its
          historical market value. There is no historical price feed.
          {stackSpec._cerebro_excluded.length > 0 && (
            <> Excluded for want of a price: {stackSpec._cerebro_excluded.join(", ")}.</>
          )}
          {tokens.dropped.length > 0 && <> Beyond the top series: {tokens.dropped.join(", ")}.</>}
        </p>
      </DatasetPanel>
    </>
  );
}
