import { useMemo } from "react";

import { MaKpi, MaKpiGrid } from "../../shared/MiniAppChrome";
import { DatasetPanel } from "../components/DatasetPanel";
import { ExportCsvButton } from "../components/ExportCsvButton";
import { AssetTable } from "../components/treasury/AssetTable";
import { ChainSwitcher, type ChainOption } from "../components/treasury/ChainSwitcher";
import { GapNote, partialNotes } from "../components/treasury/GapNote";
import { ScopeNote } from "../components/treasury/ScopeNote";
import { splitLine } from "../components/treasury/TreasuryKpis";
import { ValueHistoryPanel } from "../components/treasury/ValueHistoryPanel";
import { WalletHeader } from "../components/treasury/WalletIdentity";
import { bandRefFromSeriesId, valueStackOption } from "../model/treasuryCharts";
import { chainName, explorerUrl, TREASURY_CHAIN_IDS } from "../model/treasuryChains";
import { METHOD_CAPTION } from "../model/treasuryCopy";
import { fmtCount, fmtMonth, fmtUnitsCompact, fmtUsd } from "../model/treasuryFormat";
import {
  factBuckets,
  historyFrame,
  seriesSparkIndex,
  stackFacts,
  walletSeriesFacts,
} from "../model/treasuryHistory";
import {
  address as toAddress,
  parseCoverage,
  parseHistory,
  parseHoldings,
  parseWalletChains,
  parseWallets,
  parseWalletSeries,
} from "../model/treasuryRows";
import { assetRows, totalsOf, valuationMap } from "../model/treasuryValue";
import { useDataset, type GovViewContext } from "../sections/common";
import { datasetPhase, useIconFor, useSpotSource } from "../sections/treasury/model";

// ONE wallet on ONE chain. The same address is a treasury Safe on both
// chains, so the chain is part of the identity and one click away (the chain
// switcher) — the Ethereum page of 0x458c…5e6f used to show two months with no
// hint that the same Safe holds years of history on Gnosis Chain.

const SRC = "rpc_state_indexer month-end balances × dbt price hub (one wallet)";
/** Below this many months on a chain, point at the longer history elsewhere. */
const SHORT_HISTORY_MONTHS = 12;

function parseIdentifier(identifier: string): { chainId: number; wallet: string } {
  const sep = identifier.indexOf(":");
  const chainId = sep > 0 ? Number(identifier.slice(0, sep)) : 0;
  return { chainId: Number.isFinite(chainId) ? chainId : 0, wallet: toAddress(sep > 0 ? identifier.slice(sep + 1) : identifier) };
}

export function TreasuryWalletDetail({ ctx }: { ctx: GovViewContext }) {
  const { view, update } = ctx.treasury;
  const detailDs = useDataset(ctx, "treasury_wallet_detail");
  const positionsDs = useDataset(ctx, "treasury_wallet_positions");
  const seriesDs = useDataset(ctx, "treasury_wallet_series");
  const chainsDs = useDataset(ctx, "treasury_wallet_chains");
  const monthsDs = useDataset(ctx, "treasury_wallet_months");
  // The section's history, when it is still in the view (it usually is after
  // a drill-down): the only place the OTHER chain's first month is known.
  const sectionHistoryDs = useDataset(ctx, "treasury_history");
  const spot = useSpotSource(ctx);
  const iconFor = useIconFor(ctx);

  const fromId = parseIdentifier(ctx.state.selected_entity?.identifier ?? "");
  const detail = useMemo(() => parseWallets(detailDs)[0] ?? null, [detailDs]);
  const chainId = fromId.chainId || detail?.chainId || 1;
  const wallet = detail?.wallet || fromId.wallet;
  const positions = useMemo(() => parseHoldings(positionsDs), [positionsDs]);
  const series = useMemo(() => parseWalletSeries(seriesDs), [seriesDs]);
  const presence = useMemo(() => parseWalletChains(chainsDs), [chainsDs]);
  const months = useMemo(() => parseCoverage(monthsDs), [monthsDs]);
  const sectionHistory = useMemo(() => parseHistory(sectionHistoryDs), [sectionHistoryDs]);

  const valuations = useMemo(() => valuationMap(positions, spot, false), [positions, spot]);
  const totals = useMemo(() => totalsOf(positions, valuations, 0), [positions, valuations]);
  const assets = useMemo(() => assetRows(positions, valuations, { merge: false }), [positions, valuations]);
  const hiddenCount = positions.filter((row) => row.tokenClass === "spam").length;
  const hubUsd = detail?.navUsd ?? (positions.length > 0 ? totals.hubUsd : null);
  // A wallet with nothing valued (only unpriced or hidden tokens, or empty on
  // this chain) has an UNKNOWN value, not $0: its total is a dash.
  const valued = (detail?.pricedPositions ?? totals.counts.hub) > 0 || totals.counts.spot > 0;
  const totalUsd = hubUsd === null || !valued ? null : hubUsd + totals.spotUsd;

  const facts = useMemo(() => walletSeriesFacts(series), [series]);
  const monthsKnown = datasetPhase(ctx, "treasury_wallet_months") === "complete";
  const frame = useMemo(() => historyFrame({
    chains: [chainId],
    coverage: monthsKnown ? months : null,
    dataBuckets: factBuckets(facts, chainId),
    startAtData: true,
  }), [chainId, months, monthsKnown, facts]);
  const stack = useMemo(() => stackFacts(facts, frame, { prefix: "asset" }), [facts, frame]);
  const spec = useMemo(
    () => (stack.bands.length > 0
      ? valueStackOption({
        buckets: stack.buckets,
        bands: stack.bands,
        gaps: stack.gaps,
        partialNotes: partialNotes(frame.issuesByChain, [chainId]),
        unit: "usd",
      })
      : null),
    [stack, frame.issuesByChain, chainId],
  );
  const spark = useMemo(
    () => seriesSparkIndex(series.map((row) => ({ bucket: row.bucket, chainId: row.chainId, token: row.token, value: row.valueUsd })), frame),
    [series, frame],
  );

  const since = frame.firstByChain.get(chainId) ?? "";
  const others = presence.filter((row) => row.chainId !== chainId && row.hasPositions);
  const otherSince = (otherChain: number): string => {
    let first = "";
    for (const row of sectionHistory) {
      if (row.grain !== "wallet" || row.chainId !== otherChain || row.wallet !== wallet) continue;
      if (!first || row.bucket < first) first = row.bucket;
    }
    return first;
  };
  const shortHistory = since !== "" && frame.buckets.length < SHORT_HISTORY_MONTHS && others.length > 0;

  const options: ChainOption[] = TREASURY_CHAIN_IDS.map((id) => {
    const row = presence.find((entry) => entry.chainId === id);
    const tracked = row ? row.tracked : id === chainId;
    return {
      chainId: id,
      enabled: tracked,
      note: row?.hasPositions ? fmtUsd(row.navUsd) : tracked ? "no positions" : "not tracked",
      title: tracked ? `This wallet on ${chainName(id)}` : `This address is not in the ${chainName(id)} treasury census`,
    };
  });
  const openChain = (id: number) => ctx.onEntity("treasury_wallet", `${id}:${wallet}`);

  const detailPhase = datasetPhase(ctx, "treasury_wallet_detail");

  return (
    <div className="gov-entity gov-trs-entity">
      <WalletHeader
        kicker={`Treasury wallet · ${chainName(chainId)}`}
        address={wallet}
        label={detail?.label ?? ""}
        labelSource={detail?.labelSource ?? ""}
        isLtd={detail?.isLtd ?? false}
        explorer={explorerUrl(chainId, "address", wallet)}
        onOpenExplorer={ctx.openLink}
      />
      <div className="gov-trs-entitybar">
        <ChainSwitcher current={chainId} options={options} onSelect={openChain} ariaLabel="Wallet chain" />
        {others.map((row) => (
          <button key={row.chainId} type="button" className="gov-trs-linkbtn" onClick={() => openChain(row.chainId)}>
            Also holds {fmtUsd(row.navUsd)} on {chainName(row.chainId)} →
          </button>
        ))}
      </div>

      <DatasetPanel
        title="Wallet"
        descriptor={ctx.descriptors.treasury_wallet_detail}
        groupLoaded
        hydrationPhase={detailPhase}
        emptyLabel={`This address holds nothing on ${chainName(chainId)} at the latest snapshot.`}
      >
        <div className="gov-trs-kpis">
          <MaKpiGrid>
            <MaKpi
              label="Value"
              value={fmtUsd(totalUsd)}
              delta={valued ? splitLine(hubUsd, totals.spotUsd, spot === null) : "no valued positions on this chain"}
            />
            <MaKpi label="GNO" value={fmtUnitsCompact(detail?.gnoUnits ?? null)} />
            <MaKpi
              label="Tokens"
              value={fmtCount(detail?.tokensHeld ?? null)}
              delta={(detail?.hiddenPositions ?? 0) > 0 ? `+${fmtCount(detail?.hiddenPositions ?? null)} hidden` : undefined}
            />
            <MaKpi label="Tracked since" value={since ? fmtMonth(since) : "—"} delta={`on ${chainName(chainId)}`} />
            <MaKpi
              label="As of"
              value={detail?.asOf || "—"}
              delta={detail?.anchorBlock !== null && detail?.anchorBlock !== undefined ? `block ${fmtCount(detail.anchorBlock)}` : undefined}
            />
          </MaKpiGrid>
        </div>
        <ScopeNote />
      </DatasetPanel>

      <ValueHistoryPanel
        title="Value over time"
        chartId={`gov-trs-wallet-${chainId}`}
        descriptor={ctx.descriptors.treasury_wallet_series}
        groupLoaded
        phase={datasetPhase(ctx, "treasury_wallet_series")}
        error={ctx.hydrated.treasury_wallet_series?.error}
        spec={spec}
        sql={ctx.descriptors.treasury_wallet_series?.sql}
        sourceModel={SRC}
        coverageKnown={monthsKnown || !ctx.descriptors.treasury_wallet_series}
        isEmpty={stack.bands.length === 0}
        emptyLabel={`No hub-priced holdings on ${chainName(chainId)} in this wallet's history.`}
        onSeriesClick={(id) => {
          const ref = bandRefFromSeriesId(id);
          if (ref?.kind !== "asset") return;
          const hit = series.find((row) => (row.assetKey || row.token) === ref.key);
          if (hit) ctx.onEntity("treasury_token", `${hit.chainId}:${hit.token}`);
        }}
      >
        {shortHistory ? (
          <p className="gov-trs-callout">
            Holdings on {chainName(chainId)} start {fmtMonth(since)}
            {others.map((row) => {
              const first = otherSince(row.chainId);
              return (
                <span key={row.chainId}>
                  {" · "}
                  <button type="button" className="gov-trs-linkbtn" onClick={() => openChain(row.chainId)}>
                    {first
                      ? `${chainName(row.chainId)} history since ${fmtMonth(first)} →`
                      : `${chainName(row.chainId)} history →`}
                  </button>
                </span>
              );
            })}
          </p>
        ) : null}
        <GapNote issues={frame.issuesByChain} chains={[chainId]} />
        <p className="gov-caption">{METHOD_CAPTION}</p>
      </ValueHistoryPanel>

      <DatasetPanel
        title="Positions"
        descriptor={ctx.descriptors.treasury_wallet_positions}
        groupLoaded
        hydrationPhase={datasetPhase(ctx, "treasury_wallet_positions")}
        emptyLabel="No non-zero balances at this snapshot."
      >
        <AssetTable
          merged={assets}
          byChain={assets}
          mergeAvailable={false}
          filter={view.assetFilter}
          onFilter={(assetFilter) => update({ assetFilter })}
          showHidden={view.showHidden}
          onToggleHidden={(showHidden) => update({ showHidden })}
          hiddenCount={hiddenCount}
          onOpen={(chain, token) => ctx.onEntity("treasury_token", `${chain}:${token}`)}
          iconFor={iconFor}
          spark={spark}
          spotAt={spot?.at ?? ""}
          totalUsd={totalUsd}
          mode="wallet"
          exportSlot={(
            <ExportCsvButton
              viewId={ctx.viewId}
              datasetKey="treasury_wallet_positions"
              descriptor={ctx.descriptors.treasury_wallet_positions}
              fetchRows={ctx.fetchRows}
              scope={`treasury_wallet_${chainId}_${wallet.slice(0, 10)}`}
            />
          )}
        />
      </DatasetPanel>
    </div>
  );
}
