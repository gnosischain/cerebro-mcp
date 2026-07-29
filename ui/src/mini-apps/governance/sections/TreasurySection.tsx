import { SegmentedControl } from "../../shared/SegmentedControl";
import { TabBar } from "../../shared/TabBar";
import { TREASURY_TABS, type TreasuryTabId } from "../model/treasuryTabs";
import type { GovFilterDraft } from "../state/toolArgs";
import type { GovViewContext } from "./common";
import { HistoryView } from "./treasury/HistoryView";
import { PortfolioView } from "./treasury/PortfolioView";
import { TokensView } from "./treasury/TokensView";
import { WalletsView } from "./treasury/WalletsView";
import { useTreasuryModel } from "./treasury/shared";

// GnosisDAO treasury portfolio. Balances come from the rpc-state-indexer plane,
// each pinned to an immutable finalized block; prices are a CURRENT-spot
// CoinGecko overlay resolved client-side.
//
// Three things this view refuses to do, each because the data cannot support it:
//
//   * Sum across chains. Ethereum's latest snapshot and Gnosis Chain's are
//     years apart, so a combined total describes no single moment. Every panel
//     is per chain.
//   * Call the priced total "NAV". Only 62 of 231 held tokens carry a price. We
//     know the unpriced COUNT; we can never know the unpriced VALUE. It is a
//     lower bound and is labelled as one.
//   * Trust a token symbol. Symbols are attacker-authored: 19 distinct tokens
//     here claim "USDC", and several token names are phishing lures. The
//     address is the identity; every symbol renders through TokenIdentity,
//     sanitized, with the address shown alongside whenever it collides.
//
// This file is a shell: the toolbar, the sub-tabs, and one derivation shared by
// all four views. The views live in ./treasury/. They read ONE model so they
// cannot disagree — the hero once summed USD across two snapshots taken years
// apart precisely because each panel derived its own.
//
// Historical panels are unit-denominated. The one USD-valued history chart is
// units x TODAY's price — a constant-price revaluation, not historical market
// value — and says so.

type ChainChoice = "0" | "1" | "100";

export function TreasurySection({
  ctx,
  tab,
  onTab,
}: {
  ctx: GovViewContext;
  tab: TreasuryTabId;
  onTab: (tab: TreasuryTabId) => void;
}) {
  const filters = ctx.state.filters;
  const model = useTreasuryModel(ctx);

  const applyDraft = (patch: Partial<GovFilterDraft>) => {
    const next: GovFilterDraft = { ...ctx.draft, ...patch };
    ctx.setDraft(next);
    ctx.apply("treasury", next);
  };

  // Token and wallet drill-downs are entity views, not in-place filters: an
  // in-place filter re-queries the whole section to answer a single-token
  // question and leaves half the page unscoped, which reads as "some cards are
  // about this token, others are not".
  const openToken = (chainId: number, token: string) =>
    ctx.onEntity("treasury_token", `${chainId}:${token.toLowerCase()}`);
  const openWallet = (chainId: number, wallet: string) =>
    ctx.onEntity("treasury_wallet", `${chainId}:${wallet.toLowerCase()}`);

  return (
    <>
      <div className="gov-toolbar">
        <label>
          Chain
          <SegmentedControl<ChainChoice>
            size="sm"
            ariaLabel="Chain"
            value={String(filters.chain_id ?? 0) as ChainChoice}
            options={[
              { value: "0", label: "All" },
              { value: "1", label: "Ethereum" },
              { value: "100", label: "Gnosis Chain" },
            ]}
            onChange={(next) => applyDraft({ chain_id: Number(next) })}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={Boolean(filters.exclude_ltd)}
            onChange={(event) => applyDraft({ exclude_ltd: event.target.checked })}
          />
          Exclude Gnosis Ltd.
        </label>
      </div>

      <div className="gov-subtabs">
        <TabBar<TreasuryTabId>
          ariaLabel="Treasury views"
          tabs={TREASURY_TABS.map((entry) => ({ id: entry.id, label: entry.label }))}
          active={tab}
          onChange={onTab}
        />
      </div>

      {tab === "portfolio" && <PortfolioView ctx={ctx} model={model} onToken={openToken} />}
      {tab === "tokens" && <TokensView ctx={ctx} model={model} onToken={openToken} />}
      {tab === "wallets" && <WalletsView ctx={ctx} model={model} onWallet={openWallet} />}
      {tab === "history" && <HistoryView ctx={ctx} model={model} />}
    </>
  );
}
