import { useMemo } from "react";

import { GapNote, partialNotes } from "../../components/treasury/GapNote";
import { HistoryControls } from "../../components/treasury/HistoryControls";
import { ValueHistoryPanel } from "../../components/treasury/ValueHistoryPanel";
import { bandRefFromSeriesId, breadthOption, valueStackOption } from "../../model/treasuryCharts";
import { METHOD_CAPTION } from "../../model/treasuryCopy";
import {
  breadthSeries,
  firstPricedMarkers,
  historyFacts,
  measureAllowed,
  stackFacts,
  windowFrame,
  type ChainIssues,
} from "../../model/treasuryHistory";
import { primaryChainOf } from "../../model/treasuryWallets";
import { openAsset, type TreasuryTabProps } from "./model";

// The full history (since the census began), valued with the dbt price hub's
// price on each month-end — never today's spot. Months nothing was served or
// published for are gaps (blank, marked); partial months are drawn from what
// was served; both are named below the chart.

const HISTORY_SRC = "rpc_state_indexer month-end balances × dbt price hub";

export function HistoryTab({ ctx, model, scope, view, update, openToken, openWallet }: TreasuryTabProps) {
  const groups = ctx.state.loaded_groups ?? {};
  const measure = measureAllowed(view.stackBy, view.measure) ? view.measure : "usd";
  const frame = useMemo(() => windowFrame(scope.frame, view.range), [scope.frame, view.range]);
  const facts = useMemo(
    () => historyFacts(model.history.rows, { mode: view.stackBy, measure, chain: view.chain, exLtd: view.exLtd }),
    [model.history.rows, view.stackBy, measure, view.chain, view.exLtd],
  );
  const stack = useMemo(() => stackFacts(facts, frame, { prefix: view.stackBy }), [facts, frame, view.stackBy]);
  const markers = useMemo(
    () => (view.stackBy === "asset" ? firstPricedMarkers(model.history.rows, frame, { chain: view.chain }) : []),
    [model.history.rows, frame, view.stackBy, view.chain],
  );
  const spec = useMemo(
    () => (stack.bands.length > 0
      ? valueStackOption({
        buckets: stack.buckets,
        bands: stack.bands,
        gaps: stack.gaps,
        markers,
        partialNotes: partialNotes(scope.frame.issuesByChain, scope.chains),
        unit: measure,
        height: "440px",
      })
      : null),
    [stack, markers, measure, scope.frame.issuesByChain, scope.chains],
  );
  const breadth = useMemo(
    () => breadthSeries(model.history.rows, frame, { chain: view.chain, showHidden: view.showHidden }),
    [model.history.rows, frame, view.chain, view.showHidden],
  );
  const breadthSpec = useMemo(
    () => (frame.buckets.length > 0 ? breadthOption({ buckets: frame.buckets, ...breadth, gaps: stack.gaps }) : null),
    [frame.buckets, breadth, stack.gaps],
  );
  const coverageUnknown = model.coverage.phase === "failed"
    || (model.history.phase === "complete" && !model.coverage.descriptor && groups["treasury.history"] !== false);
  // Disclose only the months the selected range actually shows.
  const issues = useMemo<ChainIssues>(() => {
    const shown = new Set(frame.buckets);
    return new Map([...scope.frame.issuesByChain].map(([chainId, list]) => [
      chainId, list.filter((issue) => shown.has(issue.bucket)),
    ]));
  }, [scope.frame.issuesByChain, frame.buckets]);

  const onBand = (id: string) => {
    const ref = bandRefFromSeriesId(id);
    if (!ref) return;
    if (ref.kind === "chain" && (ref.chainId === 1 || ref.chainId === 100)) update({ chain: ref.chainId });
    else if (ref.kind === "asset") openAsset(scope, ref.key, openToken);
    else if (ref.kind === "wallet") {
      const group = scope.wallets.groups.find((entry) => entry.address === ref.wallet);
      openWallet(group ? primaryChainOf(group) : view.chain || 1, ref.wallet);
    }
  };

  return (
    <>
      <HistoryControls view={{ ...view, measure }} onChange={update} />
      <ValueHistoryPanel
        title={measure === "usd" ? "Holdings value over time" : "GNO held over time"}
        chartId={`gov-trs-history-${view.stackBy}-${measure}`}
        descriptor={model.history.descriptor}
        groupLoaded={groups["treasury.history"]}
        phase={model.history.phase}
        error={model.history.error}
        onRetry={() => ctx.retryGroup("treasury", "history")}
        spec={spec}
        sql={model.history.descriptor?.sql}
        sourceModel={HISTORY_SRC}
        coverageKnown={!coverageUnknown}
        isEmpty={stack.bands.length === 0}
        emptyLabel="Nothing to stack for this selection."
        onSeriesClick={onBand}
      >
        <GapNote issues={issues} chains={scope.chains} />
        <p className="gov-caption">
          {measure === "usd" ? METHOD_CAPTION : "GNO units held at each month-end (GNO token balances, both chains' contracts)."}
          {markers.length > 0 ? " Dotted markers show where an asset's hub price series begins; before it, that asset is held but not valued." : ""}
        </p>
      </ValueHistoryPanel>

      <ValueHistoryPanel
        title="Portfolio breadth"
        chartId="gov-trs-history-breadth"
        descriptor={model.history.descriptor}
        groupLoaded={groups["treasury.history"]}
        phase={model.history.phase}
        error={model.history.error}
        onRetry={() => ctx.retryGroup("treasury", "history")}
        spec={breadthSpec}
        sourceModel={HISTORY_SRC}
        coverageKnown={!coverageUnknown}
        isEmpty={frame.buckets.length === 0}
      >
        <p className="gov-caption">
          Tokens held per month by class (bars) and wallet positions (line).
          {view.showHidden ? " Hidden spam tokens are included as their own bar." : " Hidden spam tokens are excluded."}
        </p>
      </ValueHistoryPanel>
    </>
  );
}
