import { shortAddr } from "../../../../utils/format";
import { PaginatedTable } from "../../../shared/PaginatedTable";
import { finite } from "../../../shared/rowDataset";
import { DatasetPanel, GroupBanner } from "../../components/DatasetPanel";
import { TokenBoard } from "../../components/TokenBoard";
import { chainName } from "../../components/TreasuryChainCard";
import { COLUMN_LABELS, hiddenColumnsFor } from "../../model/columns";
import { fmtNum, fmtPct, GroupGate, type GovViewContext } from "../common";
import type { TreasuryModel } from "./shared";

// The token surfaces: a ranked board for reading, an evidence table for
// auditing, and the coverage panel that says what the plane cannot show.
//
// Deliberately NOT chain-scoped — a token list spanning both chains is
// meaningful because every row carries its own chain and nothing is summed.

export function TokensView({
  ctx,
  model,
  onToken,
}: {
  ctx: GovViewContext;
  model: TreasuryModel;
  onToken: (chainId: number, token: string) => void;
}) {
  const groups = ctx.state.loaded_groups ?? {};
  const retry = (group: string) => () => ctx.retryGroup("treasury", group);

  return (
    <>
      <GroupGate ctx={ctx} section="treasury" group="core">
        <DatasetPanel
          title="Holdings"
          descriptor={ctx.descriptors.treasury_holdings}
          groupLoaded={groups["treasury.core"]}
          onRetry={retry("core")}
          emptyLabel="No non-zero balances at this snapshot."
        >
          <TokenBoard
            holdings={model.holdings}
            iconFor={model.iconFor}
            sparkFor={model.sparkFor}
            onSelect={onToken}
          />
        </DatasetPanel>

        <DatasetPanel
          title="Full holdings — evidence"
          descriptor={ctx.descriptors.treasury_holdings}
          groupLoaded={groups["treasury.core"]}
          onRetry={retry("core")}
        >
          <PaginatedTable
            dataset={ctx.descriptors.treasury_holdings}
            datasetKey="treasury_holdings"
            viewId={ctx.viewId}
            fetchRows={ctx.fetchRows}
            maxHeight="420px"
            hiddenColumns={hiddenColumnsFor("treasury_holdings")}
            columnLabels={COLUMN_LABELS}
            sourceLabel="Verified balances at a pinned finalized block"
            renderCell={(column, value) => {
              if (column === "chain_id") return <span>{chainName(value)}</span>;
              if (column === "token_address") {
                return (
                  <span className="gov-mono" title={String(value ?? "")}>
                    {shortAddr(String(value ?? ""))}
                  </span>
                );
              }
              if (column === "metadata_status") {
                return value === "resolved" ? (
                  <span className="gov-caption">named</span>
                ) : (
                  <span className="gov-mono" title="symbol/decimals not observed on-chain">
                    RAW
                  </span>
                );
              }
              if (column === "balance_units") {
                // NULL whenever decimals were never observed. decimals=0 is a
                // legitimate on-chain answer, so "unknown" and "zero" must not
                // be conflated — the exact integer is in the adjacent column.
                const units = finite(value);
                return units === null ? (
                  <span className="gov-caption" title="decimals not observed — see Balance (raw)">
                    not scalable
                  </span>
                ) : (
                  <span className="gov-mono">{fmtNum(units)}</span>
                );
              }
              if (column === "supply_share") {
                const share = finite(value);
                if (share === null) return <span className="gov-caption">—</span>;
                // A holding cannot exceed its token's own supply. When it does,
                // balanceOf is lying — printing "2300%" would dress that up as
                // data.
                if (share > 1) {
                  return (
                    <span
                      className="gov-caption"
                      title="Reported balance exceeds the token's own total supply — this contract's balanceOf is not trustworthy"
                    >
                      &gt; supply
                    </span>
                  );
                }
                return <span className="gov-mono">{fmtPct(share)}</span>;
              }
              if (column === "value_usd") {
                // Typed NULL by construction — USD never enters the query. The
                // real figure comes from the client-side overlay.
                return <span className="gov-caption">—</span>;
              }
              return undefined;
            }}
          />
        </DatasetPanel>
      </GroupGate>

      <GroupGate ctx={ctx} section="treasury" group="insights">
        <GroupBanner groupLoaded={groups["treasury.insights"]} onRetry={retry("insights")} />
        <DatasetPanel
          title="What this plane can and cannot show"
          descriptor={ctx.descriptors.treasury_coverage}
          groupLoaded={groups["treasury.insights"]}
          hydrationPhase={ctx.hydrated.treasury_coverage?.phase}
          hydrationError={ctx.hydrated.treasury_coverage?.error}
          onRetry={retry("insights")}
        >
          <PaginatedTable
            dataset={ctx.descriptors.treasury_coverage}
            datasetKey="treasury_coverage"
            viewId={ctx.viewId}
            fetchRows={ctx.fetchRows}
            maxHeight="240px"
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
          <p className="gov-caption">
            Balances are exact regardless of these gaps — read from the contract at a finalized
            block and stored as integers. What is missing is display metadata and pricing. The
            server-side USD columns are typed NULLs by construction: pricing is a client-side
            overlay, so the query never fabricates a value.
          </p>
        </DatasetPanel>
      </GroupGate>
    </>
  );
}
