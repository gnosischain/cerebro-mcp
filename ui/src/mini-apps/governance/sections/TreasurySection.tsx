import { TabBar } from "../../shared/TabBar";
import { TreasuryToolbar } from "../components/treasury/TreasuryToolbar";
import { TREASURY_TABS, type TreasuryTabId } from "../model/treasuryTabs";
import type { GovViewContext } from "./common";
import { AssetsTab } from "./treasury/AssetsTab";
import { HistoryTab } from "./treasury/HistoryTab";
import { useTreasuryModel, useTreasuryScope, type TreasuryTabProps } from "./treasury/model";
import { OverviewTab } from "./treasury/OverviewTab";
import { WalletsTab } from "./treasury/WalletsTab";

// GnosisDAO treasury: ERC-20 balances at finalized blocks on Ethereum and
// Gnosis Chain, valued with the dbt price hub's daily USD price through a
// reviewed address registry (CoinGecko spot is a today-only fallback for real
// tokens the hub does not cover).
//
// A thin shell: the toolbar, the tab strip, and ONE derivation
// (./treasury/model.ts) every tab reads, so no two panels can disagree. The
// chain / Gnosis Ltd. / hidden-token controls are client-side and instant —
// the datasets always carry both chains, every wallet and every token class.

export function TreasurySection({ ctx }: { ctx: GovViewContext }) {
  const { view, update, now } = ctx.treasury;
  const model = useTreasuryModel(ctx);
  const scope = useTreasuryScope(model, view);

  // Token and wallet drill-downs are entity pages, pinned to one chain: the
  // same Safe address exists on both chains, and GNO/COW/USDC each have one
  // address per chain.
  const openToken = (chainId: number, token: string) =>
    ctx.onEntity("treasury_token", `${chainId}:${token.toLowerCase()}`);
  const openWallet = (chainId: number, wallet: string) =>
    ctx.onEntity("treasury_wallet", `${chainId}:${wallet.toLowerCase()}`);

  const badges: Partial<Record<TreasuryTabId, number>> = {
    assets: scope.assetCounts.listed + (view.showHidden ? scope.hiddenCount : 0),
    wallets: scope.wallets.groups.length,
  };
  const props: TreasuryTabProps = { ctx, model, scope, view, update, openToken, openWallet };

  return (
    <>
      <TreasuryToolbar
        view={view}
        onChange={update}
        summaries={model.summary.rows}
        hiddenCount={scope.hiddenCount}
        spot={model.spot}
        now={now}
      />
      <div className="gov-subtabs">
        <TabBar<TreasuryTabId>
          ariaLabel="Treasury views"
          scrollOnChange={false}
          tabs={TREASURY_TABS.map((entry) => ({ id: entry.id, label: entry.label, badge: badges[entry.id] }))}
          active={view.tab}
          onChange={(tab) => update({ tab })}
        />
      </div>
      {view.tab === "overview" && <OverviewTab {...props} />}
      {view.tab === "assets" && <AssetsTab {...props} />}
      {view.tab === "wallets" && <WalletsTab {...props} />}
      {view.tab === "history" && <HistoryTab {...props} />}
    </>
  );
}
