// Snapshot liquidity profile: bars per range between initialized ticks on a
// linear TICK axis (a log price axis), a band for full-range positions, a
// dashed mark at the current tick, and the tick-weighted concentration
// summary. Everything below the dataset is a client re-projection.

import { useMemo } from "react";

import { ChartCard } from "../../../components/ChartCard";
import { useTheme } from "../../../hooks/useTheme";
import { liquidityProfileOption } from "../model/chartOptions";
import { fmtInt, fmtLiquidity, fmtPct, tokenLabel } from "../model/format";
import {
  buildProfileModel, profileWindow, unitLabel, type AxisLabelContext,
} from "../model/liquidityProfile";
import { parseProfileConcentration, parseProfileRanges, type PoolDetailView } from "../model/parseRows";
import { useDataset, type PlxViewContext } from "../sections/common";
import { DatasetPanel } from "./DatasetPanel";
import { DatasetInfo } from "./InfoPopover";
import { PlxTable } from "./PlxTable";

const BAND_LABELS: Record<string, string> = {
  "1pct": "±1%",
  "5pct": "±5%",
  "10pct": "±10%",
  full_range: "full range",
};

export function LiquidityProfilePanel({ ctx, detail, yLog }: {
  ctx: PlxViewContext;
  detail: PoolDetailView;
  yLog: boolean;
}) {
  const { isDark } = useTheme();
  const { client } = ctx;
  const profileDs = useDataset(ctx, "pool_profile_at");
  const concentrationDs = useDataset(ctx, "pool_profile_concentration");
  const parsed = useMemo(() => parseProfileRanges(profileDs), [profileDs]);
  const bands = useMemo(() => parseProfileConcentration(concentrationDs), [concentrationDs]);
  const currentTick = parsed.currentTick ?? detail.currentTick;
  const window = useMemo(
    () => profileWindow(currentTick, client.zoom, parsed.ranges),
    [currentTick, client.zoom, parsed.ranges],
  );
  const model = useMemo(
    () => buildProfileModel({ ranges: parsed.ranges, currentTick, window }),
    [parsed.ranges, currentTick, window],
  );
  const sym0 = tokenLabel(detail.token0Symbol, detail.token0);
  const sym1 = tokenLabel(detail.token1Symbol, detail.token1);
  const labelCtx: AxisLabelContext = {
    mode: client.axis,
    currentTick,
    dec0: detail.token0Decimals,
    dec1: detail.token1Decimals,
    inverted: client.inverted,
  };
  const unit = unitLabel({
    mode: client.axis, sym0, sym1, dec0: detail.token0Decimals, dec1: detail.token1Decimals, inverted: client.inverted,
  });
  const spec = useMemo(
    () => liquidityProfileOption({ model, labelCtx, unit, yLog, isDark }),
    // labelCtx / unit are derived from the same inputs listed here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [model, client.axis, client.inverted, currentTick, detail.token0Decimals, detail.token1Decimals, unit, yLog, isDark],
  );
  const groups = ctx.state.loaded_groups ?? {};
  const retry = () => ctx.retryGroup("pool", "profile");
  const rawUnits = detail.token0Decimals === null || detail.token1Decimals === null;

  return (
    <>
      <DatasetPanel
        title="Liquidity profile"
        descriptor={ctx.descriptors.pool_profile_at}
        groupLoaded={groups["pool.profile"]}
        hydrationPhase={ctx.hydrated.pool_profile_at?.phase}
        hydrationError={ctx.hydrated.pool_profile_at?.error}
        onRetry={retry}
        meta={<DatasetInfo datasetKey="pool_profile_at" descriptor={ctx.descriptors.pool_profile_at} />}
        emptyLabel="No initialized ticks were published for this pool at this date — no profile can be drawn."
      >
        <div className="plx-strip">
          <span><strong>{fmtInt(parsed.ranges.length)}</strong> ranges</span>
          <span><strong>{fmtPct(model.shareInWindow)}</strong> of L·width inside the window</span>
          {model.fullRangeBand && (
            <span title="Drawn as a band across the whole axis; never sets the zoom window">
              full-range L <strong>{fmtLiquidity(model.fullRangeBand.liquidity)}</strong>
            </span>
          )}
          {model.rangesOutside > 0 && <span>{fmtInt(model.rangesOutside)} outside the window</span>}
          {parsed.matchesState !== null && (
            <span className={parsed.matchesState ? "plx-ok" : "plx-warn"}>
              {parsed.matchesState ? "matches state liquidity" : "does NOT match state liquidity"}
            </span>
          )}
          {rawUnits && <span className="plx-warn">prices in raw units</span>}
        </div>
        <ChartCard chartId="plx-profile" hideId spec={spec} renderer="svg" />
        <div className="plx-hint">
          Bars are the ranges between consecutive initialized ticks; the axis is linear in tick (a log price axis).
          Scroll to zoom. Dashed edges mark ranges clipped by the window or reaching the full-range boundary.
        </div>
      </DatasetPanel>
      <DatasetPanel
        title="Concentration"
        descriptor={ctx.descriptors.pool_profile_concentration}
        groupLoaded={groups["pool.profile"]}
        hydrationPhase={ctx.hydrated.pool_profile_concentration?.phase}
        hydrationError={ctx.hydrated.pool_profile_concentration?.error}
        onRetry={retry}
        meta={<DatasetInfo datasetKey="pool_profile_concentration" descriptor={ctx.descriptors.pool_profile_concentration} />}
        emptyLabel="Not measured — the pool was not probed at this date."
      >
        <div className="plx-conc">
          {bands.map((band) => (
            <div key={band.band} className="plx-conc__cell">
              <span className="plx-conc__band">{BAND_LABELS[band.band] ?? band.band}</span>
              <strong>{fmtPct(band.share)}</strong>
              <span className="plx-conc__meta">
                {band.rangesInBand === null ? "" : `${fmtInt(band.rangesInBand)} ranges`}
              </span>
            </div>
          ))}
          {bands[0] && (
            <div className="plx-conc__cell">
              <span className="plx-conc__band">L at current tick</span>
              <strong>{fmtLiquidity(bands[0].liquidityAtCurrent)}</strong>
            </div>
          )}
        </div>
        <div className="plx-hint">
          Tick-weighted: Σ L × overlap / Σ L × width. A full-range position dominates the denominator by construction.
        </div>
      </DatasetPanel>
      <DatasetPanel
        title="Ranges"
        descriptor={ctx.descriptors.pool_profile_at}
        groupLoaded={groups["pool.profile"]}
        onRetry={retry}
        emptyLabel="No ranges at this date."
      >
        <PlxTable
          datasetKey="pool_profile_at"
          descriptor={ctx.descriptors.pool_profile_at}
          viewId={ctx.viewId}
          fetchRows={ctx.fetchRows}
          maxHeight="340px"
        />
      </DatasetPanel>
    </>
  );
}
