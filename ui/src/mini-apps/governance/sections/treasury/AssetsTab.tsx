import { useMemo } from "react";

import { DatasetPanel } from "../../components/DatasetPanel";
import { ExportCsvButton } from "../../components/ExportCsvButton";
import { AssetTable, classification } from "../../components/treasury/AssetTable";
import { fmtCount } from "../../model/treasuryFormat";
import { GroupGate } from "../common";
import type { TreasuryTabProps } from "./model";

// Every asset the treasury holds: search, class chips, sort, merge chains.
// Below it, the classification in counts — how many tokens fall in each
// class, and why the hidden ones are hidden — never their names.

export function AssetsTab({ ctx, model, scope, view, update, openToken }: TreasuryTabProps) {
  const groups = ctx.state.loaded_groups ?? {};
  const classes = useMemo(() => classification(scope.holdings), [scope.holdings]);
  const valuedTotal = scope.hubNav === null ? null : scope.hubNav + scope.totals.spotUsd;
  return (
    <GroupGate ctx={ctx} section="treasury" group="core">
      <DatasetPanel
        title="Assets"
        descriptor={model.holdings.descriptor}
        groupLoaded={groups["treasury.core"]}
        hydrationPhase={model.holdings.phase}
        hydrationError={model.holdings.error}
        onRetry={() => ctx.retryGroup("treasury", "core")}
        emptyLabel="No token balances at this snapshot."
      >
        <AssetTable
          merged={scope.assetsMerged}
          byChain={scope.assetsByChain}
          mergeAvailable={view.chain === 0}
          filter={view.assetFilter}
          onFilter={(assetFilter) => update({ assetFilter })}
          showHidden={view.showHidden}
          hiddenCount={scope.hiddenCount}
          onOpen={openToken}
          iconFor={model.iconFor}
          spark={scope.spark}
          spotAt={model.spot?.at ?? ""}
          totalUsd={valuedTotal}
          exportSlot={(
            <ExportCsvButton
              viewId={ctx.viewId}
              datasetKey="treasury_holdings"
              descriptor={model.holdings.descriptor}
              fetchRows={ctx.fetchRows}
              scope={`treasury_${view.chain === 0 ? "all" : view.chain}`}
              label="Export CSV (all classes)"
            />
          )}
        />
      </DatasetPanel>

      <DatasetPanel
        title="How tokens are classified"
        descriptor={model.holdings.descriptor}
        groupLoaded={groups["treasury.core"]}
        hydrationPhase={model.holdings.phase}
      >
        <div className="gov-trs-classes">
          <dl>
            {classes.byClass.map((entry) => (
              <div key={entry.key} className="gov-trs-classes__item">
                <dt>{entry.label}</dt>
                <dd>
                  <strong>{fmtCount(entry.count)}</strong>
                  <span>{entry.description}</span>
                </dd>
              </div>
            ))}
          </dl>
          <dl>
            {classes.byReason.map((entry) => (
              <div key={entry.key} className="gov-trs-classes__item">
                <dt>Hidden · {entry.label}</dt>
                <dd>
                  <strong>{fmtCount(entry.count)}</strong>
                  <span>{entry.description}</span>
                </dd>
              </div>
            ))}
          </dl>
        </div>
        <p className="gov-caption">
          Classes come from a reviewed address registry: the hub price is matched by address, never by
          the on-chain symbol, which anyone can set. The CSV export carries every class and reason.
        </p>
      </DatasetPanel>
    </GroupGate>
  );
}
