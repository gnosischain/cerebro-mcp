import { useMemo } from "react";

import { ChartCard } from "../../../../components/ChartCard";
import { DatasetPanel } from "../../components/DatasetPanel";
import { TreasuryChainCard } from "../../components/TreasuryChainCard";
import { compositionTreemapOption, concentrationBarOption } from "../../model/chartOptions";
import { compositionItems, concentration, priceCoverage } from "../../model/treasuryPricing";
import { fmtPct, GroupGate, type GovViewContext } from "../common";
import { scopeHoldings, useChainScope, type TreasuryModel } from "./shared";

// "How much is there." One card per chain, then the concentration headline and
// the tail that headline hides.
//
// Every figure below the cards is scoped to ONE chain. Summing USD across
// chains would add Ethereum's 2026-07 snapshot to Gnosis Chain's 2022-12 one —
// a total that describes no single moment.

const TREASURY_SRC = "rpc_state_indexer.v_treasury_balances";

function fmtUsd(value: number | null): string {
  if (value === null) return "—";
  return `$${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export function PortfolioView({
  ctx,
  model,
  onToken,
}: {
  ctx: GovViewContext;
  model: TreasuryModel;
  onToken: (chainId: number, token: string) => void;
}) {
  const groups = ctx.state.loaded_groups ?? {};
  const retryCore = () => ctx.retryGroup("treasury", "core");

  const chainId = useChainScope(model.summaryRows, ctx.state.filters.chain_id);
  const scoped = useMemo(
    () => scopeHoldings(model.holdings, chainId),
    [model.holdings, chainId],
  );

  const coverage = useMemo(() => priceCoverage(scoped), [scoped]);
  const lead = useMemo(() => concentration(scoped), [scoped]);

  const concentrationSpec = useMemo(
    () => (lead
      ? concentrationBarOption({
        leadLabel: lead.label,
        leadUsd: lead.usd,
        restUsd: Math.max(0, coverage.usd - lead.usd),
      })
      : null),
    [lead, coverage.usd],
  );

  // With one holding at ~80% of priced value a whole-portfolio treemap is a
  // single tile; excluding the leader is what makes the tail legible at all.
  const tailSpec = useMemo(() => {
    if (!lead) return null;
    const items = compositionItems(scoped, { exclude: lead.token, cap: 24 });
    return items.length ? compositionTreemapOption(items) : null;
  }, [scoped, lead]);

  return (
    <GroupGate ctx={ctx} section="treasury" group="core">
      <div className="gov-grid-2">
        {model.summaryRows.map((row) => (
          <TreasuryChainCard
            key={String(row.chain_id)}
            row={row}
            holdings={model.holdings}
            priceSource={model.priceSource}
          />
        ))}
      </div>
      {model.summaryRows.length > 1 && (
        <p className="gov-caption">
          Each chain is indexed independently and states its own as-of date and anchor block.
          Figures are never summed across chains: the snapshots are not contemporaneous, so a
          combined total would not describe any single moment.
        </p>
      )}

      {concentrationSpec && lead && (
        <DatasetPanel
          title="Concentration"
          descriptor={ctx.descriptors.treasury_holdings}
          groupLoaded={groups["treasury.core"]}
          onRetry={retryCore}
        >
          <ChartCard
            chartId="gov-treasury-concentration"
            hideId
            sourceModel={TREASURY_SRC}
            spec={concentrationSpec}
          />
          <p className="gov-caption">
            {lead.label} is {fmtPct(lead.share)} of the priced total ({fmtUsd(coverage.usd)}).
            Priced total is a <strong>lower bound</strong>: {coverage.total - coverage.priced} of{" "}
            {coverage.total} held tokens carry no price, and the value of an unpriced token is
            unknown, not zero.
          </p>
        </DatasetPanel>
      )}

      {tailSpec && lead && (
        <DatasetPanel
          title={`Composition excluding ${lead.label}`}
          descriptor={ctx.descriptors.treasury_holdings}
          groupLoaded={groups["treasury.core"]}
          onRetry={retryCore}
        >
          <ChartCard
            chartId="gov-treasury-tail"
            hideId
            sourceModel={TREASURY_SRC}
            spec={tailSpec}
            onEvents={{
              click: (params: unknown) => {
                const id = (params as { data?: { id?: string } }).data?.id;
                // The folded tail is a synthetic tile, not a token.
                if (!id || id === "other") return;
                // Chain comes from the CLICKED token: the same symbol exists on
                // both chains under different addresses.
                const hit = scoped.find((held) => held.token === id.toLowerCase());
                if (hit) onToken(hit.chainId, hit.token);
              },
            }}
          />
          <div className="gov-unpriced">
            {coverage.total - coverage.priced} held tokens carry no price and cannot be sized
            here. They are not zero; they are unmeasured.
          </div>
        </DatasetPanel>
      )}
    </GroupGate>
  );
}
