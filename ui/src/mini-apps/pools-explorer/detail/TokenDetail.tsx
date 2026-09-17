import { useMemo } from "react";

import { ChartCard } from "../../../components/ChartCard";
import { MaIdentity, MaSkeletonKpiGrid, MaSkeletonRows } from "../../shared/MiniAppChrome";
import { shortAddr } from "../../../utils/format";
import { DatasetPanel } from "../components/DatasetPanel";
import { DatasetInfo } from "../components/InfoPopover";
import { KpiRow } from "../components/KpiRow";
import { PlxTable } from "../components/PlxTable";
import { TokenLabel } from "../components/TokenLabel";
import { tokenPoolsShareOption } from "../model/chartOptions";
import { fmtAmountWithOverlay, fmtDate, fmtInt } from "../model/format";
import { parseTokenDetail, parseTokenPools } from "../model/parseRows";
import { resolveDecimals, resolveTokenLabel } from "../model/tokenOverlay";
import { useDataset, type PlxViewContext } from "../sections/common";

// One token: metadata resolution, the pools holding it, and each pool's share
// of the token's reserves — in the token's OWN unit (raw when decimals are
// unknown), never USD. Liquidity (L) is never summed across pairs.

export function TokenDetail({ ctx }: { ctx: PlxViewContext }) {
  const groups = ctx.state.loaded_groups ?? {};
  const detailDs = useDataset(ctx, "token_detail");
  const detail = useMemo(() => parseTokenDetail(detailDs), [detailDs]);
  const poolsDs = useDataset(ctx, "token_pools");
  // `reserve_token_units` comes from the server (scaled by the row's own
  // `token_decimals`, NULL when unknown) — nothing is scaled client-side.
  const pools = useMemo(() => parseTokenPools(poolsDs), [poolsDs]);
  // The chart's unit label may fall back to the chain-state symbol; the chart
  // itself carries no marker, so the header (which does) stays the disclosure.
  const unit = detail ? resolveTokenLabel(detail.address, detail.symbol, ctx.overlay).text : "";
  const shareSpec = useMemo(() => tokenPoolsShareOption(pools, unit), [pools, unit]);

  if (!detail) {
    const coreLoaded = groups["token.core"];
    if (coreLoaded === false || (coreLoaded === undefined && !detailDs)) {
      return (
        <div className="plx-skel" aria-busy="true" aria-label="Loading token">
          <MaSkeletonKpiGrid />
          <MaSkeletonRows count={6} />
        </div>
      );
    }
    return (
      <DatasetPanel
        title="Token"
        descriptor={ctx.descriptors.token_detail}
        groupLoaded={coreLoaded}
        onRetry={() => ctx.retryGroup("token", "core")}
        emptyLabel={(
          <span>
            <strong>{ctx.state.selected_entity?.identifier ?? "This address"}</strong> is not an asset of any configured pool.
          </span>
        )}
      >
        <span />
      </DatasetPanel>
    );
  }

  // Decimals may now come from chain state; when they do the figure is marked
  // rather than reading as a publication-verified amount.
  const decimals = resolveDecimals(detail.decimals, detail.address, ctx.overlay);
  const total = fmtAmountWithOverlay(
    detail.totalReserveRaw, detail.decimals, detail.totalReserveUnits,
    decimals.source === "overlay" ? decimals.decimals : null,
  );
  const totalSuffix = total.rawUnits ? " (raw)" : total.chain ? " (chain state)" : "";
  return (
    <div className="plx-entity">
      <div className="plx-header">
        <MaIdentity
          label={detail.entityLabel || `token · ${shortAddr(detail.address)}`}
          value={detail.name || detail.symbol || detail.address}
          onCopy={() => { void navigator.clipboard?.writeText(detail.address); }}
          rightSlot={(
            <span className="plx-badges">
              <TokenLabel address={detail.address} symbol={detail.symbol} resolved={detail.resolved} overlay={ctx.overlay} />
              <span className="plx-badge" title="Metadata resolution status from v_token_metadata_current">{detail.resolutionStatus || "unknown"}</span>
            </span>
          )}
        />
        <code className="plx-header__addr" title={detail.address}>{detail.address}</code>
        <KpiRow
          items={[
            {
              label: `Decimals${decimals.source === "overlay" ? " (chain state)" : ""}`,
              value: decimals.decimals === null ? "unknown" : fmtInt(decimals.decimals),
            },
            { label: "Pools", value: fmtInt(detail.poolsCount) },
            { label: "CL pools", value: fmtInt(detail.clPools) },
            { label: "Reserves-only pools", value: fmtInt(detail.reservesOnlyPools) },
            { label: "Live pools", value: fmtInt(detail.livePools) },
            { label: "Probed pools", value: fmtInt(detail.probedPools) },
            { label: `Reserves across pools${totalSuffix}`, value: total.text },
            { label: "Reserves as of", value: fmtDate(detail.reservesAsOf) },
          ]}
          meta={<DatasetInfo datasetKey="token_detail" descriptor={ctx.descriptors.token_detail} />}
        />
      </div>
      <DatasetPanel
        title="Share of reserves by pool"
        descriptor={ctx.descriptors.token_pools}
        groupLoaded={groups["token.core"]}
        onRetry={() => ctx.retryGroup("token", "core")}
        emptyLabel="No pool holds this token at the latest reserves publication."
        meta={<DatasetInfo datasetKey="token_pools" descriptor={ctx.descriptors.token_pools} />}
      >
        <ChartCard chartId="plx-token-share" hideId spec={shareSpec} />
        <div className="plx-hint">Share = this pool's reserve of the token ÷ the token's reserves across all its pools (same unit).</div>
      </DatasetPanel>
      <DatasetPanel
        title="Pools holding this token"
        descriptor={ctx.descriptors.token_pools}
        groupLoaded={groups["token.core"]}
        onRetry={() => ctx.retryGroup("token", "core")}
        emptyLabel="No pools."
      >
        <PlxTable
          datasetKey="token_pools"
          descriptor={ctx.descriptors.token_pools}
          viewId={ctx.viewId}
          fetchRows={ctx.fetchRows}
          onEntity={ctx.onEntity}
          overlay={ctx.overlay}
          entityAddress={detail.address}
          onPageLoaded={ctx.onPageLoaded}
          maxHeight="520px"
        />
      </DatasetPanel>
    </div>
  );
}
