import { useMemo } from "react";
import { ChartCard } from "../../../components/ChartCard";
import { shortAddr } from "../../../utils/format";
import { PaginatedTable } from "../../shared/PaginatedTable";
import { finite, rowsToObjects } from "../../shared/rowDataset";
import { DatasetPanel, GroupBanner } from "../components/DatasetPanel";
import { donutOption, horizontalBarOption } from "../model/chartOptions";
import { COLUMN_LABELS, hiddenColumnsFor } from "../model/columns";
import { fmtNum, fmtPct, GroupGate, KpiRow, useDataset, type GovViewContext } from "./common";

// Treasury: verified ERC-20 balances for the GnosisDAO wallet set, read from
// the rpc-state-indexer plane. Every figure is pinned to an immutable finalized
// block, and the anchor is shown rather than hidden — that attributability is
// the whole point of this plane over a portfolio API.
//
// Two things are deliberately NOT claimed here:
//   * No USD. There is no price feed yet, so every USD column is NULL and
//     renders as an em-dash. A fabricated $0 would be worse than no number.
//   * No value ranking. Without prices, balances in different units are not
//     comparable. The default order surfaces what can be displayed truthfully
//     (resolved metadata) and ranks by share of the token's OWN supply, which
//     is at least dimensionless. The caption says so.
//
// A token whose decimals were never observed is NEVER scaled: decimals=0 is a
// legitimate on-chain answer, so "unknown" and "zero" must not be conflated —
// scaling by 10^0 produces a plausible-looking wrong number. Those rows show
// the exact integer with a RAW badge instead.

const TREASURY_SRC = "rpc_state_indexer.v_treasury_balances";
const WALLET_BARS = 12;

/** Chain id → display name. Ids come from the data; unknown ids show as-is. */
const CHAIN_NAMES: Record<string, string> = { "1": "Ethereum", "100": "Gnosis Chain" };

function chainName(value: unknown): string {
  const key = String(value ?? "");
  return CHAIN_NAMES[key] ?? (key ? `Chain ${key}` : "—");
}

/** fmtNum(null) is "0" because Number(null) === 0 — which would render an
 * unwired USD figure as a real $0. Anything nullable goes through this. */
function fmtOrDash(value: number | null): string {
  return value === null ? "—" : fmtNum(value);
}

export function TreasurySection({ ctx }: { ctx: GovViewContext }) {
  const groups = ctx.state.loaded_groups ?? {};
  // One row per chain: chains publish independently and their latest snapshots
  // are months apart, so a single blended KPI row would silently mix a current
  // snapshot with a stale one. Each chain states its own as-of and anchor.
  const summaryDs = useDataset(ctx, "treasury_summary");
  const summaryRows = useMemo(() => rowsToObjects(summaryDs), [summaryDs]);
  const holdings = ctx.descriptors.treasury_holdings;
  const retryInsights = () => ctx.retryGroup("treasury", "insights");

  const walletDs = useDataset(ctx, "treasury_by_wallet");
  const walletRows = useMemo(() => rowsToObjects(walletDs), [walletDs]);

  // GNO by wallet: the one holding with an unambiguous governance meaning and
  // fully resolved metadata on mainnet. Stands in for a composition donut,
  // which cannot exist without prices.
  const gnoSpec = useMemo(() => donutOption(
    walletRows
      .map((row) => ({
        name: shortAddr(String(row.wallet_address ?? "")),
        value: finite(row.gno_units) ?? 0,
      }))
      .filter((row) => row.value > 0),
  ), [walletRows]);

  const walletSpec = useMemo(() => horizontalBarOption(
    walletRows
      .map((row) => ({
        name: shortAddr(String(row.wallet_address ?? "")),
        value: finite(row.tokens_held) ?? 0,
      }))
      .filter((row) => row.value > 0)
      .slice(0, WALLET_BARS),
    "Tokens held",
  ), [walletRows]);

  const totalPositions = summaryRows.reduce(
    (sum, row) => sum + (finite(row.positions) ?? 0), 0,
  );

  // The commonest day-one state, and the one most likely to be misread as
  // "the treasury is empty". Say why instead of rendering empty chrome.
  if (summaryRows.length > 0 && totalPositions === 0) {
    return (
      <GroupGate ctx={ctx} section="treasury" group="core">
        <p className="gov-caption">
          No non-zero balances published for this scope yet. The treasury job publishes
          per chain and each chain advances independently, so a chain with no rows here
          has not been indexed through to a snapshot yet — it does not mean the treasury
          holds nothing.
        </p>
      </GroupGate>
    );
  }

  return (
    <>
      <GroupGate ctx={ctx} section="treasury" group="core">
        {summaryRows.map((row) => {
          const asOf = String(row.as_of ?? "").slice(0, 10);
          const anchor = finite(row.anchor_block);
          return (
            <KpiRow
              key={String(row.chain_id)}
              items={[
                { label: "Chain", value: chainName(row.chain_id) },
                { label: "Tokens held", value: fmtOrDash(finite(row.tokens_held)) },
                { label: "Named", value: fmtOrDash(finite(row.tokens_named)) },
                { label: "Wallets", value: fmtOrDash(finite(row.wallets_tracked)) },
                { label: "GNO", value: fmtOrDash(finite(row.gno_units)) },
                { label: "GNO ex-Ltd.", value: fmtOrDash(finite(row.gno_units_ex_ltd)) },
                // NULL until a price feed exists — an em-dash, never 0.
                { label: "NAV (USD)", value: fmtOrDash(finite(row.nav_usd)) },
              ]}
              meta={
                asOf ? (
                  <span className="gov-caption">
                    As of {asOf}
                    {anchor !== null ? <> · block <span className="gov-mono">{fmtNum(anchor)}</span></> : null}
                  </span>
                ) : null
              }
            />
          );
        })}
        {summaryRows.length > 1 && (
          <p className="gov-caption">
            Each chain is indexed independently and states its own as-of date and anchor
            block. Figures are not summed across chains: the snapshots are not
            contemporaneous, so a combined total would not describe any single moment.
          </p>
        )}

        <div className="gov-grid-2">
          <DatasetPanel
            title="GNO by wallet"
            descriptor={ctx.descriptors.treasury_by_wallet}
            groupLoaded={groups["treasury.core"]}
            onRetry={() => ctx.retryGroup("treasury", "core")}
            emptyLabel="No GNO balances at this snapshot."
          >
            <ChartCard
              chartId="gov-treasury-gno"
              hideId
              sql={ctx.descriptors.treasury_by_wallet?.sql}
              sourceModel={TREASURY_SRC}
              spec={gnoSpec}
            />
          </DatasetPanel>
          <DatasetPanel
            title="Distinct tokens per wallet"
            descriptor={ctx.descriptors.treasury_by_wallet}
            groupLoaded={groups["treasury.core"]}
            onRetry={() => ctx.retryGroup("treasury", "core")}
          >
            <ChartCard
              chartId="gov-treasury-wallets"
              hideId
              sql={ctx.descriptors.treasury_by_wallet?.sql}
              sourceModel={TREASURY_SRC}
              spec={walletSpec}
            />
          </DatasetPanel>
        </div>

        <DatasetPanel
          title="Holdings by token"
          descriptor={holdings}
          groupLoaded={groups["treasury.core"]}
          onRetry={() => ctx.retryGroup("treasury", "core")}
          emptyLabel="No non-zero balances at this snapshot."
        >
          <PaginatedTable
            dataset={holdings}
            datasetKey="treasury_holdings"
            viewId={ctx.viewId}
            fetchRows={ctx.fetchRows}
            maxHeight="520px"
            hiddenColumns={hiddenColumnsFor("treasury_holdings")}
            columnLabels={COLUMN_LABELS}
            sourceLabel="Verified balances at a pinned finalized block"
            renderCell={(column, value) => {
              if (column === "chain_id") return <span>{chainName(value)}</span>;
              if (column === "token_address") {
                return <span className="gov-mono" title={String(value ?? "")}>{shortAddr(String(value ?? ""))}</span>;
              }
              if (column === "symbol") {
                // No symbol means metadata was never observed. Show the gap.
                return value ? <span>{String(value)}</span> : <span className="gov-caption">unknown</span>;
              }
              if (column === "metadata_status") {
                return value === "resolved"
                  ? <span className="gov-caption">named</span>
                  : <span className="gov-mono" title="symbol/decimals not observed on-chain">RAW</span>;
              }
              if (column === "balance_units") {
                // NULL whenever decimals were not observed. Never substitute a
                // scaled guess: decimals=0 is a legitimate on-chain answer, so
                // "unknown" and "zero" must not be conflated. The exact integer
                // is always present in the adjacent Balance (raw) column.
                const units = finite(value);
                if (units === null) {
                  return (
                    <span className="gov-caption" title="decimals not observed — see Balance (raw)">
                      not scalable
                    </span>
                  );
                }
                return <span className="gov-mono">{fmtNum(units)}</span>;
              }
              if (column === "balance_total_raw") {
                return <span className="gov-mono" title="exact on-chain integer">{String(value ?? "—")}</span>;
              }
              if (column === "supply_share") {
                const share = finite(value);
                if (share === null) return <span className="gov-caption">—</span>;
                // A holding cannot exceed the token's own supply. When it does,
                // balanceOf is lying — the classic spoofed-token shape is a
                // constant balance returned to every caller, so N wallets each
                // "hold" 100% and the total lands near N x supply. Printing
                // "2300%" as if it were a real share would dress that up as data.
                if (share > 1) {
                  return (
                    <span
                      className="gov-caption"
                      title="Reported balance exceeds the token's own total supply — the contract's balanceOf is not trustworthy"
                    >
                      &gt; supply
                    </span>
                  );
                }
                return <span className="gov-mono">{fmtPct(share)}</span>;
              }
              if (column === "value_usd") return <span className="gov-caption">—</span>;
              return undefined;
            }}
          />
        </DatasetPanel>
        <p className="gov-caption">
          Ordered by whether the token could be named on-chain, then by share of that
          token&apos;s own total supply. This is a display order, not a ranking by value:
          without a price feed, balances in different units are not comparable. Rows marked
          RAW have no observed decimals and are shown as exact integers.
        </p>
      </GroupGate>

      <GroupGate ctx={ctx} section="treasury" group="insights">
        <GroupBanner groupLoaded={groups["treasury.insights"]} onRetry={retryInsights} />
        <DatasetPanel
          title="What this plane can and cannot show"
          descriptor={ctx.descriptors.treasury_coverage}
          groupLoaded={groups["treasury.insights"]}
          hydrationPhase={ctx.hydrated.treasury_coverage?.phase}
          hydrationError={ctx.hydrated.treasury_coverage?.error}
          onRetry={retryInsights}
        >
          <PaginatedTable
            dataset={ctx.descriptors.treasury_coverage}
            datasetKey="treasury_coverage"
            viewId={ctx.viewId}
            fetchRows={ctx.fetchRows}
            maxHeight="260px"
            columnLabels={COLUMN_LABELS}
            sourceLabel="Coverage of the held token set at this snapshot"
            renderCell={(column, value) => {
              if (column === "pct_known") {
                const pct = finite(value);
                return <span className="gov-mono">{pct === null ? "—" : fmtPct(pct)}</span>;
              }
              return undefined;
            }}
          />
        </DatasetPanel>
        <p className="gov-caption">
          Balances are exact regardless of these gaps — they are read from the contract at a
          finalized block and stored as integers. What is missing is display metadata
          (symbol, decimals) and pricing. Unpriced is why no USD figure appears anywhere on
          this tab, and why there is no value ranking.
        </p>
      </GroupGate>
    </>
  );
}
