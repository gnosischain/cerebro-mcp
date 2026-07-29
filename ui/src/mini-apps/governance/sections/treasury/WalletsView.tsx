import { useMemo } from "react";

import { ChartCard } from "../../../../components/ChartCard";
import { DatasetPanel, GroupBanner } from "../../components/DatasetPanel";
import { WalletBoard } from "../../components/WalletBoard";
import { chainName } from "../../components/TreasuryChainCard";
import { walletStackOption } from "../../model/chartOptions";
import { walletSeries } from "../../model/treasuryHistory";
import { GroupGate, type GovViewContext } from "../common";
import { useChainScope, type TreasuryModel } from "./shared";

// "Who holds what." This view exists because `treasury_by_wallet` was being
// loaded by the server and rendered nowhere — the data was fetched and thrown
// away, which is why per-wallet holdings were impossible to see.

const HISTORY_SRC = "rpc_state_indexer.v_treasury_balances (monthly)";

export function WalletsView({
  ctx,
  model,
  onWallet,
}: {
  ctx: GovViewContext;
  model: TreasuryModel;
  onWallet: (chainId: number, wallet: string) => void;
}) {
  const groups = ctx.state.loaded_groups ?? {};
  const chainId = useChainScope(model.summaryRows, ctx.state.filters.chain_id);

  const scopedWallets = useMemo(
    () => (chainId === null
      ? model.wallets
      : model.wallets.filter((wallet) => wallet.chainId === chainId)),
    [model.wallets, chainId],
  );

  // One chart, for the scoped chain only — never two chains on one axis.
  const wallets = useMemo(
    () => (chainId === null ? [] : walletSeries(model.walletHistRows, chainId)),
    [model.walletHistRows, chainId],
  );
  const walletSpec = useMemo(
    () => walletStackOption(wallets, { unitLabel: "GNO" }),
    [wallets],
  );

  return (
    <>
      <GroupGate ctx={ctx} section="treasury" group="core">
        <DatasetPanel
          title={`${chainName(chainId)} — treasury wallets`}
          descriptor={ctx.descriptors.treasury_by_wallet}
          groupLoaded={groups["treasury.core"]}
          onRetry={() => ctx.retryGroup("treasury", "core")}
          emptyLabel="No tracked wallets at this snapshot."
        >
          <WalletBoard
            wallets={scopedWallets}
            ltdExcluded={Boolean(ctx.state.filters.exclude_ltd)}
            onSelect={onWallet}
          />
          {!ctx.state.filters.chain_id && model.wallets.length > scopedWallets.length && (
            <p className="gov-caption">
              Scoped to {chainName(chainId)}, the chain with the newest snapshot. The other chain
              is indexed independently and years behind — pick it in the toolbar to see its
              wallets. {model.wallets.length - scopedWallets.length} wallet
              {model.wallets.length - scopedWallets.length === 1 ? " is" : "s are"} not shown here.
            </p>
          )}
        </DatasetPanel>
      </GroupGate>

      <GroupGate ctx={ctx} section="treasury" group="history">
        <GroupBanner
          groupLoaded={groups["treasury.history"]}
          onRetry={() => ctx.retryGroup("treasury", "history")}
        />
        {wallets.length > 0 && (
          <DatasetPanel
            title={`${chainName(chainId)} — GNO by wallet over time`}
            descriptor={ctx.descriptors.treasury_wallet_history}
            groupLoaded={groups["treasury.history"]}
            onRetry={() => ctx.retryGroup("treasury", "history")}
          >
            <ChartCard
              chartId={`gov-treasury-wallets-${chainId}`}
              hideId
              sql={ctx.descriptors.treasury_wallet_history?.sql}
              sourceModel={HISTORY_SRC}
              spec={walletSpec}
            />
            <p className="gov-caption">
              Every band is GNO, so the stack total is meaningful. The Gnosis Ltd. band carries a
              fixed colour and &ldquo;Other&rdquo; is the folded tail of smaller wallets, always
              last.
            </p>
          </DatasetPanel>
        )}
      </GroupGate>
    </>
  );
}
