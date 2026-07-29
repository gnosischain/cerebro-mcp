import { useMemo } from "react";

import { ChartCard } from "../../../components/ChartCard";
import { shortAddr } from "../../../utils/format";
import { PaginatedTable } from "../../shared/PaginatedTable";
import { finite, rowsToObjects } from "../../shared/rowDataset";
import { DatasetPanel } from "../components/DatasetPanel";
import { TreasuryFocusBar } from "../components/TreasuryFocusBar";
import { chainName } from "../components/TreasuryChainCard";
import { walletStackOption } from "../model/chartOptions";
import { COLUMN_LABELS } from "../model/columns";
import { walletSeries } from "../model/treasuryHistory";
import { priceFor, priceSourceFrom, usdValue } from "../model/treasuryPricing";
import {
  firstRow,
  fmtNum,
  fmtPct,
  KpiRow,
  pickNumber,
  pickString,
  useDataset,
  type GovViewContext,
} from "../sections/common";

// ONE token, on ONE chain. Everything on this page is about that token — which
// is the whole reason it replaced the in-place `asset` filter: that filter
// re-queried the section and scoped only half of it, so some cards spoke about
// the token and others still showed the portfolio.
//
// The identifier is `<chain>:<address>` and the chain is load-bearing: GNO,
// COW and WETH each exist on both chains under different addresses.

const SRC = "rpc_state_indexer.v_treasury_balances";

/** `fmtNum(null)` returns "0" (Number(null) === 0). Counts go through this. */
function fmtOrDash(value: number | null): string {
  return value === null ? "—" : fmtNum(value);
}

export function TreasuryTokenDetail({ ctx }: { ctx: GovViewContext }) {
  const detail = firstRow(ctx, "treasury_token_detail");
  const holdersDs = useDataset(ctx, "treasury_token_holders");
  const seriesDs = useDataset(ctx, "treasury_token_holder_series");

  const identifier = ctx.state.selected_entity?.identifier ?? "";
  const [chainText, addressFromId] = identifier.split(":");
  const chainId = Number(chainText) || finite(detail?.chain_id) || 0;
  const token = (pickString(detail, ["token_address"]) || addressFromId || "").toLowerCase();

  const priceSource = useMemo(
    () => priceSourceFrom(ctx.state.price_overlay, ctx.state.price_overlay_at),
    [ctx.state.price_overlay, ctx.state.price_overlay_at],
  );
  const price = priceFor(priceSource, chainId, token);
  const units = pickNumber(detail, ["balance_units"]);
  const iconUrl = (ctx.state.icon_overlay ?? {})[String(chainId)]?.[token] ?? "";

  // `symbol_collisions` counts OTHER held tokens claiming this symbol, measured
  // over the whole chain. It cannot be derived here: this page only ever sees
  // one token's rows, and 18 of the 19 addresses claiming "USDC" are elsewhere.
  const collisions = pickNumber(detail, ["symbol_collisions"]);
  const supplyShare = pickNumber(detail, ["supply_share"]);

  const holderRows = useMemo(() => rowsToObjects(holdersDs), [holdersDs]);
  const seriesRows = useMemo(() => rowsToObjects(seriesDs), [seriesDs]);
  const symbol = pickString(detail, ["symbol"]);

  const wallets = useMemo(() => walletSeries(seriesRows, chainId), [seriesRows, chainId]);
  const walletSpec = useMemo(
    () => walletStackOption(wallets, { unitLabel: symbol || shortAddr(token) }),
    [wallets, symbol, token],
  );

  return (
    <div className="gov-entity">
      <TreasuryFocusBar
        holding={{
          chainId,
          token,
          symbol,
          // Server-measured, not inferred from the rows on this page.
          ambiguous: (collisions ?? 0) > 0,
          wallets: pickNumber(detail, ["wallets_holding"]),
          supplyShare,
          usd: usdValue(units, price),
        }}
        iconUrl={iconUrl}
      />

      <DatasetPanel
        title="Token"
        descriptor={ctx.descriptors.treasury_token_detail}
        groupLoaded
        emptyLabel="No non-zero balance of this token at the latest snapshot. It may have been held historically — the chart below still covers that."
      >
        <KpiRow
          items={[
            { label: "Chain", value: chainName(chainId) },
            { label: "Held (units)", value: fmtOrDash(units) },
            {
              label: "Value at spot",
              value: price === null ? "unpriced" : `$${fmtNum(usdValue(units, price))}`,
            },
            { label: "Wallets holding", value: fmtOrDash(pickNumber(detail, ["wallets_holding"])) },
            {
              label: "Others claiming this symbol",
              value: collisions === null ? "—" : fmtNum(collisions),
            },
            {
              // A holding cannot exceed the token's own supply. When it does,
              // balanceOf is lying and a percentage would dress that up as data.
              label: "Share of supply",
              value: supplyShare === null
                ? "—"
                : supplyShare > 1
                  ? "> supply"
                  : fmtPct(supplyShare),
            },
          ]}
          meta={`As of ${pickString(detail, ["as_of"]).slice(0, 10) || "—"} · anchor block ${
            fmtOrDash(pickNumber(detail, ["anchor_block"]))
          } · ${pickString(detail, ["metadata_status"]) === "resolved" ? "metadata resolved" : "metadata NOT resolved"}`}
        />
        {(collisions ?? 0) > 0 && (
          <p className="gov-caption">
            {collisions} other held token{collisions === 1 ? "" : "s"} on {chainName(chainId)} also
            report the symbol <strong>{symbol || "—"}</strong>. The symbol identifies nothing here;
            the address does. A high count is not evidence either way — the real token and every
            spoof of it report the same number.
          </p>
        )}
      </DatasetPanel>

      <DatasetPanel
        title="Wallets holding it"
        descriptor={ctx.descriptors.treasury_token_holders}
        groupLoaded
        emptyLabel="No wallet holds a non-zero balance at the latest snapshot."
      >
        <PaginatedTable
          dataset={ctx.descriptors.treasury_token_holders}
          datasetKey="treasury_token_holders"
          viewId={ctx.viewId}
          fetchRows={ctx.fetchRows}
          maxHeight="360px"
          hiddenColumns={["chain_id", "value_usd"]}
          columnLabels={COLUMN_LABELS}
          sourceLabel="Verified balances at a pinned finalized block"
          onCellClick={(column, _value, row) => {
            if (column !== "wallet_address") return;
            const columns = ctx.descriptors.treasury_token_holders?.columns ?? [];
            const index = columns.findIndex((entry) => entry.name === "wallet_address");
            const wallet = index >= 0 ? String(row[index] ?? "") : "";
            if (wallet) ctx.onEntity("treasury_wallet", `${chainId}:${wallet.toLowerCase()}`);
          }}
          renderCell={(column, value) => {
            if (column === "wallet_address") {
              return (
                <span className="gov-mono" title={String(value ?? "")}>
                  {shortAddr(String(value ?? ""))}
                </span>
              );
            }
            if (column === "is_ltd") {
              return finite(value) === 1 || value === true
                ? <span className="gov-ltd-badge">Ltd.</span>
                : <span className="gov-caption">—</span>;
            }
            if (column === "balance_units") {
              const units_ = finite(value);
              return units_ === null
                ? <span className="gov-caption" title="decimals not observed">not scalable</span>
                : <span className="gov-mono">{fmtNum(units_)}</span>;
            }
            if (column === "treasury_share") {
              const share = finite(value);
              return <span className="gov-mono">{share === null ? "—" : fmtPct(share)}</span>;
            }
            return undefined;
          }}
        />
        <p className="gov-caption">
          Share is of the <strong>treasury&apos;s own</strong> position in this token, not of its
          supply. A contract that returns a constant balance to every caller gives each of the{" "}
          {holderRows.length || "n"} wallets an identical share — that pattern is a spoof
          signature, not a distribution.
        </p>
      </DatasetPanel>

      {wallets.length > 0 && (
        <DatasetPanel
          title="Wallet split over time"
          descriptor={ctx.descriptors.treasury_token_holder_series}
          groupLoaded
          hydrationPhase={ctx.hydrated.treasury_token_holder_series?.phase}
          hydrationError={ctx.hydrated.treasury_token_holder_series?.error}
        >
          <ChartCard
            chartId={`gov-token-wallets-${chainId}`}
            hideId
            sql={ctx.descriptors.treasury_token_holder_series?.sql}
            sourceModel={SRC}
            spec={walletSpec}
          />
          <p className="gov-caption">
            Month-end snapshots, one chain. Every band is the same token, so the stack total is
            meaningful; &ldquo;Other&rdquo; is the folded tail of smaller wallets and always sorts
            last.
          </p>
        </DatasetPanel>
      )}
    </div>
  );
}
