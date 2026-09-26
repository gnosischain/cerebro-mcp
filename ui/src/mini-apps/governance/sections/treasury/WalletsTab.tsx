import { useMemo } from "react";

import { DatasetPanel } from "../../components/DatasetPanel";
import { GapNote, partialNotes } from "../../components/treasury/GapNote";
import { ValueHistoryPanel } from "../../components/treasury/ValueHistoryPanel";
import { WalletTable } from "../../components/treasury/WalletTable";
import { bandRefFromSeriesId, valueStackOption } from "../../model/treasuryCharts";
import { historyFacts, stackFacts } from "../../model/treasuryHistory";
import { primaryChainOf } from "../../model/treasuryWallets";
import { GroupGate } from "../common";
import type { TreasuryTabProps } from "./model";

// Who holds what: every wallet address (one row each, a chip per chain), and
// the value by wallet over the full history.

const HISTORY_SRC = "rpc_state_indexer month-end balances × dbt price hub (wallet grain)";

export function WalletsTab({ ctx, model, scope, view, openWallet }: TreasuryTabProps) {
  const groups = ctx.state.loaded_groups ?? {};
  const facts = useMemo(
    () => historyFacts(model.history.rows, { mode: "wallet", measure: "usd", chain: view.chain, exLtd: view.exLtd }),
    [model.history.rows, view.chain, view.exLtd],
  );
  const stack = useMemo(() => stackFacts(facts, scope.frame, { prefix: "wallet" }), [facts, scope.frame]);
  const spec = useMemo(
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
  const coverageUnknown = model.coverage.phase === "failed"
    || (model.history.phase === "complete" && !model.coverage.descriptor && groups["treasury.history"] !== false);

  return (
    <>
      <GroupGate ctx={ctx} section="treasury" group="core">
        <DatasetPanel
          title="Treasury wallets"
          descriptor={model.wallets.descriptor}
          groupLoaded={groups["treasury.core"]}
          hydrationPhase={model.wallets.phase}
          hydrationError={model.wallets.error}
          onRetry={() => ctx.retryGroup("treasury", "core")}
          emptyLabel="No tracked wallets at this snapshot."
        >
          <WalletTable
            grouping={scope.wallets}
            chainFilter={view.chain}
            exLtd={view.exLtd}
            onOpen={openWallet}
          />
        </DatasetPanel>
      </GroupGate>

      <ValueHistoryPanel
        title="Value by wallet over time"
        chartId="gov-trs-wallets-history"
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
        onSeriesClick={(id) => {
          const ref = bandRefFromSeriesId(id);
          if (ref?.kind !== "wallet") return;
          const group = scope.wallets.groups.find((entry) => entry.address === ref.wallet);
          openWallet(group ? primaryChainOf(group) : view.chain || 1, ref.wallet);
        }}
      >
        <GapNote issues={scope.frame.issuesByChain} chains={scope.chains} />
        <p className="gov-caption">
          Hub-priced value per wallet at each month-end; the five largest wallets over the period get
          their own band and the rest fold into &ldquo;Other&rdquo;. Click a band to open the wallet.
        </p>
      </ValueHistoryPanel>
    </>
  );
}
