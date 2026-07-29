import { useMemo } from "react";

import { ChartCard } from "../../../components/ChartCard";
import { MaIdentity } from "../../shared/MiniAppChrome";
import { finite, rowsToObjects } from "../../shared/rowDataset";
import { DatasetPanel } from "../components/DatasetPanel";
import { TokenBoard } from "../components/TokenBoard";
import { chainName } from "../components/TreasuryChainCard";
import { constantPriceStackOption } from "../model/chartOptions";
import { sparkValues, tokenSeries } from "../model/treasuryHistory";
import {
  priceCoverage,
  priceFor,
  pricedHoldings,
  priceSourceFrom,
  type PricedHolding,
} from "../model/treasuryPricing";
import {
  firstRow,
  fmtNum,
  KpiRow,
  pickNumber,
  pickString,
  useDataset,
  type GovViewContext,
} from "../sections/common";

// ONE wallet, on ONE chain. This is the only page where a wallet's total can be
// valued at all: `treasury_by_wallet` (the Wallets board) aggregates away the
// per-token composition, so there it can only price GNO and says so. Here the
// composition exists.
//
// 23 of the 24 census wallets exist verbatim on BOTH chains, which is why the
// chain is part of the identifier and is printed in the header.

const SRC = "rpc_state_indexer.v_treasury_balances";

function fmtOrDash(value: number | null): string {
  return value === null ? "—" : fmtNum(value);
}

function fmtUsd(value: number | null): string {
  if (value === null) return "—";
  return `$${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export function TreasuryWalletDetail({ ctx }: { ctx: GovViewContext }) {
  const detail = firstRow(ctx, "treasury_wallet_detail");
  const positionsDs = useDataset(ctx, "treasury_wallet_positions");
  const seriesDs = useDataset(ctx, "treasury_wallet_series");

  const identifier = ctx.state.selected_entity?.identifier ?? "";
  const [chainText, addressFromId] = identifier.split(":");
  const chainId = Number(chainText) || finite(detail?.chain_id) || 0;
  const wallet = (pickString(detail, ["wallet_address"]) || addressFromId || "").toLowerCase();

  const priceSource = useMemo(
    () => priceSourceFrom(ctx.state.price_overlay, ctx.state.price_overlay_at),
    [ctx.state.price_overlay, ctx.state.price_overlay_at],
  );
  const icons = ctx.state.icon_overlay ?? {};
  const iconFor = useMemo(
    () => (chain: number, token: string) => icons[String(chain)]?.[token.toLowerCase()] ?? "",
    [icons],
  );

  const positionRows = useMemo(() => rowsToObjects(positionsDs), [positionsDs]);
  const seriesRows = useMemo(() => rowsToObjects(seriesDs), [seriesDs]);

  // `pricedHoldings` derives `ambiguous` from the SET it is handed, which here
  // is one wallet's tokens — a spoofed USDC held only by this wallet would come
  // back unflagged even though 18 other addresses claim the symbol chain-wide.
  // `symbol_collisions` is measured server-side over the whole chain, so it
  // overrides the local inference rather than supplementing it.
  const positions = useMemo<PricedHolding[]>(() => {
    const collisions = new Map<string, number>();
    for (const row of positionRows) {
      const token = String(row.token_address ?? "").trim().toLowerCase();
      if (token) collisions.set(token, finite(row.symbol_collisions) ?? 0);
    }
    return pricedHoldings(positionRows, priceSource).map((held) => ({
      ...held,
      ambiguous: held.symbol !== "" && (collisions.get(held.token) ?? 0) > 0,
    }));
  }, [positionRows, priceSource]);

  const coverage = useMemo(() => priceCoverage(positions), [positions]);

  const sparkIndex = useMemo(() => {
    const index = new Map<string, number[]>();
    for (const held of positions) {
      const values = sparkValues(seriesRows, chainId, held.token);
      if (values.length) index.set(held.token, values);
    }
    return index;
  }, [seriesRows, positions, chainId]);
  const sparkFor = useMemo(
    () => (_chain: number, token: string) => sparkIndex.get(token.toLowerCase()) ?? [],
    [sparkIndex],
  );

  const priceOf = useMemo(
    () => (token: string) => priceFor(priceSource, chainId, token),
    [priceSource, chainId],
  );
  const tokens = useMemo(
    () => tokenSeries(seriesRows, chainId, priceOf),
    [seriesRows, chainId, priceOf],
  );
  const stackSpec = useMemo(() => constantPriceStackOption(tokens.series, {}), [tokens.series]);

  return (
    <div className="gov-entity">
      <MaIdentity
        label={`TREASURY WALLET · ${chainName(chainId)}`}
        value={wallet}
        onCopy={() => void navigator.clipboard?.writeText(wallet)}
      />

      <DatasetPanel
        title="Wallet"
        descriptor={ctx.descriptors.treasury_wallet_detail}
        groupLoaded
        emptyLabel="This wallet holds nothing at the latest snapshot on this chain."
      >
        <KpiRow
          items={[
            { label: "Chain", value: chainName(chainId) },
            { label: "Tokens held", value: fmtOrDash(pickNumber(detail, ["tokens_held"])) },
            { label: "Named", value: fmtOrDash(pickNumber(detail, ["tokens_named"])) },
            { label: "GNO", value: fmtOrDash(pickNumber(detail, ["gno_units"])) },
            { label: "Priced value", value: fmtUsd(coverage.usd) },
            {
              label: "Gnosis Ltd.",
              value: finite(detail?.is_ltd) === 1 || detail?.is_ltd === true ? "yes" : "no",
            },
          ]}
          meta={`As of ${pickString(detail, ["as_of"]).slice(0, 10) || "—"} · anchor block ${
            fmtOrDash(pickNumber(detail, ["anchor_block"]))
          } · ${coverage.priced} of ${coverage.total} positions priced`}
        />
        <p className="gov-caption">
          Priced value is a <strong>lower bound</strong>: {coverage.total - coverage.priced} of{" "}
          {coverage.total} positions carry no quote, and an unpriced position is unknown, not zero.
        </p>
      </DatasetPanel>

      <DatasetPanel
        title="Positions"
        descriptor={ctx.descriptors.treasury_wallet_positions}
        groupLoaded
        emptyLabel="No non-zero balances at this snapshot."
      >
        <TokenBoard
          holdings={positions}
          iconFor={iconFor}
          sparkFor={sparkFor}
          onSelect={(chain, token) => ctx.onEntity("treasury_token", `${chain}:${token}`)}
        />
      </DatasetPanel>

      {tokens.series.length > 0 && (
        <DatasetPanel
          title="Positions revalued at today's price"
          descriptor={ctx.descriptors.treasury_wallet_series}
          groupLoaded
          hydrationPhase={ctx.hydrated.treasury_wallet_series?.phase}
          hydrationError={ctx.hydrated.treasury_wallet_series?.error}
        >
          <ChartCard
            chartId={`gov-wallet-stack-${chainId}`}
            hideId
            sql={ctx.descriptors.treasury_wallet_series?.sql}
            sourceModel={SRC}
            spec={stackSpec}
          />
          <p className="gov-caption">
            Past balances valued at <strong>today&apos;s</strong> spot price — a constant-price
            revaluation showing how this wallet&apos;s shape changed, <strong>not</strong> its
            historical market value. There is no historical price feed.
            {stackSpec._cerebro_excluded.length > 0 && (
              <> Excluded for want of a price: {stackSpec._cerebro_excluded.join(", ")}.</>
            )}
            {tokens.dropped.length > 0 && <> Beyond the top series: {tokens.dropped.join(", ")}.</>}
          </p>
        </DatasetPanel>
      )}
    </div>
  );
}
