import { DatasetPanel } from "../components/DatasetPanel";
import { DatasetInfo } from "../components/InfoPopover";
import { PlxTable } from "../components/PlxTable";
import { PoolFilterBar } from "../components/PoolFilterBar";
import { fmtDate, fmtInt } from "../model/format";
import { GroupGate, type PlxViewContext } from "./common";

// Pool directory: server-paged, server-filtered. Every filter is a section
// reload; the table itself never filters client-side.

export function PoolsSection({ ctx }: { ctx: PlxViewContext }) {
  const groups = ctx.state.loaded_groups ?? {};
  const descriptor = ctx.descriptors.pool_directory;
  const total = descriptor?.stats?.source_rows ?? descriptor?.stats?.row_count ?? null;
  return (
    <>
      <PoolFilterBar ctx={ctx} />
      <GroupGate ctx={ctx} section="pools" group="core">
        <DatasetPanel
          title="Pool directory"
          descriptor={descriptor}
          groupLoaded={groups["pools.core"]}
          onRetry={() => ctx.retryGroup("pools", "core")}
          emptyLabel="No pool matches these filters at this publication."
          meta={(
            <span className="plx-section-actions">
              <span className="plx-chip">
                {fmtInt(total)} pools · as of {ctx.state.as_of ? fmtDate(ctx.state.as_of) : "latest publication"}
              </span>
              <DatasetInfo datasetKey="pool_directory" descriptor={descriptor} />
            </span>
          )}
        >
          <PlxTable
            datasetKey="pool_directory"
            descriptor={descriptor}
            viewId={ctx.viewId}
            fetchRows={ctx.fetchRows}
            onEntity={ctx.onEntity}
            overlay={ctx.overlay}
            onPageLoaded={ctx.onPageLoaded}
            maxHeight="640px"
          />
          <div className="plx-hint">
            Click a pool for its profile, or a token for its pools. Prices are token1 per token0 — raw units unless both decimals are known.
            A <span className="plx-chainmark">chain</span> marker means the symbol or decimals were read from current chain state, not from a verified snapshot.
          </div>
        </DatasetPanel>
      </GroupGate>
    </>
  );
}
