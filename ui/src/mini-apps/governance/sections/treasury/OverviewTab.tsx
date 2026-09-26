import { useMemo, useRef, useState } from "react";

import { ChartCard } from "../../../../components/ChartCard";
import { SegmentedControl } from "../../../shared/SegmentedControl";
import { DatasetPanel } from "../../components/DatasetPanel";
import { DataNotes, dataNotes } from "../../components/treasury/DataNotes";
import { GapNote, partialNotes } from "../../components/treasury/GapNote";
import { ScopeNote } from "../../components/treasury/ScopeNote";
import { TreasuryKpis } from "../../components/treasury/TreasuryKpis";
import { ValueCell } from "../../components/treasury/ValueCell";
import { ValueHistoryPanel } from "../../components/treasury/ValueHistoryPanel";
import { WalletIdentity } from "../../components/treasury/WalletIdentity";
import { walletValueText } from "../../components/treasury/WalletTable";
import { bandRefFromSeriesId, compositionTreemapOption, valueStackOption } from "../../model/treasuryCharts";
import { fmtShare } from "../../model/treasuryFormat";
import { historyFacts, stackFacts } from "../../model/treasuryHistory";
import { assetDisplayLabel, compositionItems } from "../../model/treasuryValue";
import { primaryChainOf } from "../../model/treasuryWallets";
import { GroupGate } from "../common";
import { openAsset, type TreasuryTabProps } from "./model";

// "How much is there, where, and how did it get here." Headline tiles with
// the hub/spot split, the value over time, what it is made of, and — in words
// — what the figures leave out.

const HISTORY_SRC = "rpc_state_indexer month-end balances × dbt price hub";
const TOP_N = 8;

type OverviewStack = "chain" | "class";

export function OverviewTab({ ctx, model, scope, view, update, openToken, openWallet }: TreasuryTabProps) {
  const groups = ctx.state.loaded_groups ?? {};
  const [stackBy, setStackBy] = useState<OverviewStack>(view.chain === 0 ? "chain" : "class");
  const [composition, setComposition] = useState<"all" | "exgno">("all");

  const facts = useMemo(
    () => historyFacts(model.history.rows, { mode: stackBy, measure: "usd", chain: view.chain, exLtd: view.exLtd }),
    [model.history.rows, stackBy, view.chain, view.exLtd],
  );
  const stack = useMemo(() => stackFacts(facts, scope.frame, { prefix: stackBy }), [facts, scope.frame, stackBy]);
  const historySpec = useMemo(
    () => (stack.bands.length > 0
      ? valueStackOption({
        buckets: stack.buckets,
        bands: stack.bands,
        gaps: stack.gaps,
        partialNotes: partialNotes(scope.frame.issuesByChain, scope.chains),
        unit: "usd",
      })
      : null),
    [stack, scope.frame.issuesByChain, scope.chains],
  );

  const items = useMemo(
    () => compositionItems(scope.assetsMerged, { excludeAssetKey: composition === "exgno" ? "GNO" : undefined }),
    [scope.assetsMerged, composition],
  );
  const treemapSpec = useMemo(() => (items.length > 0 ? compositionTreemapOption(items) : null), [items]);
  const treemapHandler = useRef<(id: string) => void>(() => {});
  treemapHandler.current = (id: string) => {
    if (id && id !== "other") openAsset(scope, id, openToken);
  };
  const treemapEvents = useMemo(() => ({
    click: (params: unknown) => {
      const id = (params as { data?: { id?: unknown } }).data?.id;
      if (typeof id === "string") treemapHandler.current(id);
    },
  }), []);

  const topAssets = scope.assetsMerged.filter((asset) => !asset.hidden).slice(0, TOP_N);
  const topWallets = scope.wallets.groups.slice(0, TOP_N);
  const valuedTotal = scope.hubNav === null ? null : scope.hubNav + scope.totals.spotUsd;

  const notes = dataNotes({
    chains: scope.chains,
    issues: scope.frame.issuesByChain,
    totals: scope.totals,
    holdings: scope.holdings,
    summaries: model.summary.rows,
    spotAt: model.spot?.at ?? "",
    ltdOnlyHidden: scope.ltdOnly,
  });
  const coverageUnknown = model.coverage.phase === "failed"
    || (model.history.phase === "complete" && !model.coverage.descriptor && groups["treasury.history"] !== false);

  return (
    <>
      <ScopeNote />
      <GroupGate ctx={ctx} section="treasury" group="core">
        <DatasetPanel
          title={view.exLtd ? "Treasury holdings — excluding Gnosis Ltd." : "Treasury holdings"}
          descriptor={model.summary.descriptor}
          groupLoaded={groups["treasury.core"]}
          hydrationPhase={model.summary.phase}
          hydrationError={model.summary.error}
          onRetry={() => ctx.retryGroup("treasury", "core")}
          emptyLabel="No treasury snapshot is available for this selection."
        >
          <TreasuryKpis
            hubUsd={scope.hubNav}
            spotUsd={scope.totals.spotUsd}
            spotPending={model.spot === null}
            chains={scope.summaries.map((row) => ({
              chainId: row.chainId,
              hubUsd: scope.hubNavByChain.get(row.chainId) ?? null,
              spotUsd: scope.totals.byChain[row.chainId]?.spotUsd ?? 0,
            }))}
            showChainTiles={view.chain === 0 && scope.summaries.length > 1}
            gnoUnits={scope.gnoUnits}
            gnoUnitsExLtd={scope.gnoUnitsExLtd}
            exLtd={view.exLtd}
            wallets={scope.wallets.groups.length}
            walletPairs={scope.wallets.pairs}
            assets={scope.assetCounts}
          />
        </DatasetPanel>
      </GroupGate>

      <ValueHistoryPanel
        title="Holdings value over time"
        chartId={`gov-trs-overview-${stackBy}`}
        descriptor={model.history.descriptor}
        groupLoaded={groups["treasury.history"]}
        phase={model.history.phase}
        error={model.history.error}
        onRetry={() => ctx.retryGroup("treasury", "history")}
        spec={historySpec}
        sql={model.history.descriptor?.sql}
        sourceModel={HISTORY_SRC}
        coverageKnown={!coverageUnknown}
        isEmpty={stack.bands.length === 0}
        emptyLabel="No hub-priced holdings in this selection's history."
        meta={(
          <SegmentedControl<OverviewStack>
            size="sm"
            ariaLabel="Stack holdings by"
            value={stackBy}
            options={[
              { value: "chain", label: "By chain" },
              { value: "class", label: "By asset class" },
            ]}
            onChange={setStackBy}
          />
        )}
        onSeriesClick={(id) => {
          const ref = bandRefFromSeriesId(id);
          if (ref?.kind === "chain" && (ref.chainId === 1 || ref.chainId === 100)) update({ chain: ref.chainId });
        }}
      >
        <GapNote issues={scope.frame.issuesByChain} chains={scope.chains} />
        <p className="gov-caption">
          Hub-priced value at each month-end, full history.
          {stackBy === "chain" && view.chain === 0 ? " Click a chain's band to filter to it." : ""}{" "}
          <button type="button" className="gov-trs-linkbtn" onClick={() => update({ tab: "history" })}>
            Explore history →
          </button>
        </p>
      </ValueHistoryPanel>

      <GroupGate ctx={ctx} section="treasury" group="core">
        <DatasetPanel
          title={composition === "exgno" ? "Composition excluding GNO" : "Composition"}
          descriptor={model.holdings.descriptor}
          groupLoaded={groups["treasury.core"]}
          hydrationPhase={model.holdings.phase}
          hydrationError={model.holdings.error}
          onRetry={() => ctx.retryGroup("treasury", "core")}
          meta={(
            <SegmentedControl<"all" | "exgno">
              size="sm"
              ariaLabel="Composition scope"
              value={composition}
              options={[
                { value: "all", label: "All" },
                { value: "exgno", label: "Excluding GNO" },
              ]}
              onChange={setComposition}
            />
          )}
        >
          {treemapSpec ? (
            <ChartCard
              chartId={`gov-trs-composition-${composition}`}
              hideId
              spec={treemapSpec}
              sourceModel="treasury_holdings (valued assets, merged across chains)"
              onEvents={treemapEvents}
            />
          ) : (
            <div className="gov-empty">No valued holdings in this selection.</div>
          )}
          <p className="gov-caption">
            Tile area is value; unpriced assets have no honest area and are counted in the notes below.
            Click a tile to open the asset.
          </p>
        </DatasetPanel>

        <div className="gov-grid-2">
          <DatasetPanel
            title="Top assets"
            descriptor={model.holdings.descriptor}
            groupLoaded={groups["treasury.core"]}
            hydrationPhase={model.holdings.phase}
            meta={(
              <button type="button" className="gov-trs-linkbtn" onClick={() => update({ tab: "assets" })}>
                View all {scope.assetCounts.listed} →
              </button>
            )}
          >
            <ol className="gov-trs-top">
              {topAssets.map((asset) => (
                <li key={asset.key}>
                  <button type="button" onClick={() => openAsset(scope, asset.key, openToken)}>
                    <span className="gov-trs-top__name">{assetDisplayLabel(asset)}</span>
                    <ValueCell kind={asset.kind} usd={asset.usd} hubUsd={asset.hubUsd} spotUsd={asset.spotUsd} spotAt={model.spot?.at ?? ""} proxy={asset.proxy} />
                    <span className="gov-trs-top__share">
                      {asset.usd !== null && valuedTotal ? fmtShare(asset.usd / valuedTotal) : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          </DatasetPanel>
          <DatasetPanel
            title="Top wallets"
            descriptor={model.wallets.descriptor}
            groupLoaded={groups["treasury.core"]}
            hydrationPhase={model.wallets.phase}
            meta={(
              <button type="button" className="gov-trs-linkbtn" onClick={() => update({ tab: "wallets" })}>
                View all {scope.wallets.groups.length} →
              </button>
            )}
          >
            <ol className="gov-trs-top">
              {topWallets.map((group) => (
                <li key={group.address}>
                  <button type="button" onClick={() => openWallet(primaryChainOf(group), group.address)}>
                    <span className="gov-trs-top__name">
                      <WalletIdentity address={group.address} label={group.label} labelSource={group.labelSource} isLtd={group.isLtd} />
                    </span>
                    <span className="gov-trs-value">{walletValueText(group)}</span>
                    <span className="gov-trs-top__share">{group.chains.length > 1 ? `${group.chains.length} chains` : ""}</span>
                  </button>
                </li>
              ))}
            </ol>
          </DatasetPanel>
        </div>

        <DatasetPanel title="Data notes" descriptor={model.holdings.descriptor} groupLoaded={groups["treasury.core"]} hydrationPhase={model.holdings.phase}>
          <DataNotes notes={notes} />
        </DatasetPanel>
      </GroupGate>
    </>
  );
}
