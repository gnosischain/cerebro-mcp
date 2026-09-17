// Liquidity profile OVER TIME (`pool.heatmap`, on demand). The group is
// excluded from the app's background streaming: it is requested when this
// panel mounts (the "Over time" view) and again when the window changes,
// deduped by `${scope_id}|${address}|${window}` so a re-render never
// re-fires it. A failed query ships a zero-row stub whose provenance carries
// the real error — that renders as an error card with a FORCED retry, never
// as the empty state.

import { useEffect, useMemo, useRef, useState } from "react";

import { ChartCard } from "../../../components/ChartCard";
import { useTheme } from "../../../hooks/useTheme";
import { datasetError } from "../../shared/datasetError";
import { profileHeatmapOption } from "../model/chartOptions";
import { fmtInt, tokenLabel } from "../model/format";
import { labelFor, unitLabel } from "../model/liquidityProfile";
import type { PoolDetailView } from "../model/parseRows";
import {
  HEATMAP_WINDOWS, buildProfileHeatmap, isHeatmapWindow, parseHeatmapRows,
  type HeatmapAxisMode, type HeatmapWindow,
} from "../model/profileHeatmap";
import { MaSection } from "../../shared/MiniAppChrome";
import { useDataset, type PlxViewContext } from "../sections/common";
import { DatasetInfo } from "./InfoPopover";
import { LiquidityLegend } from "./LiquidityLegend";

export function ProfileHeatmapPanel({ ctx, detail }: { ctx: PlxViewContext; detail: PoolDetailView }) {
  const { isDark } = useTheme();
  const { state, client } = ctx;
  const stateWindow = state.heatmap_window;
  const [window, setWindow] = useState<HeatmapWindow>(isHeatmapWindow(stateWindow) ? stateWindow : "1y");
  const [axisMode, setAxisMode] = useState<HeatmapAxisMode>("tick");
  useEffect(() => {
    if (isHeatmapWindow(stateWindow)) setWindow(stateWindow);
  }, [stateWindow]);

  const descriptor = ctx.descriptors.pool_profile_heatmap;
  const loaded = state.loaded_groups?.["pool.heatmap"];
  const groupFailed = ctx.failedGroups.includes("pool.heatmap");
  const error = datasetError(descriptor);

  // HEATMAP-HOOK: one request per (scope, pool, window); skipped when the
  // server already holds this window (a previous open, or the dev fixture).
  const requestRef = useRef("");
  const requestKey = `${state.scope_id}|${detail.address}|${window}`;
  useEffect(() => {
    if (requestRef.current === requestKey) return;
    if (loaded === true && descriptor && stateWindow === window) {
      requestRef.current = requestKey;
      return;
    }
    requestRef.current = requestKey;
    ctx.onLoadHeatmap(window);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey]);

  const retry = () => {
    requestRef.current = "";
    ctx.onLoadHeatmap(window, { force: true });
  };

  const heatmapDs = useDataset(ctx, "pool_profile_heatmap");
  const rows = useMemo(() => parseHeatmapRows(heatmapDs), [heatmapDs]);
  const labelCtx = {
    mode: client.axis === "pct" ? ("price" as const) : client.axis,
    currentTick: detail.currentTick,
    dec0: detail.token0Decimals,
    dec1: detail.token1Decimals,
    inverted: client.inverted,
  };
  const model = useMemo(
    () => buildProfileHeatmap({ rows, axisMode, labelFor: (tick) => labelFor(tick, labelCtx) }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, axisMode, client.axis, client.inverted, detail.currentTick, detail.token0Decimals, detail.token1Decimals],
  );
  const sym0 = tokenLabel(detail.token0Symbol, detail.token0);
  const sym1 = tokenLabel(detail.token1Symbol, detail.token1);
  const unit = axisMode === "relative"
    ? "% from that date's current price"
    : unitLabel({ mode: labelCtx.mode, sym0, sym1, dec0: detail.token0Decimals, dec1: detail.token1Decimals, inverted: client.inverted });
  const spec = useMemo(() => profileHeatmapOption({ model, isDark, unit }), [model, isDark, unit]);

  const body = (() => {
    if (groupFailed || error) {
      return (
        <div className="plx-panel-error" role="alert">
          <div>
            <strong>The liquidity heatmap failed to load.</strong>
            <span>{error || "The heatmap dataset group failed to load."}</span>
          </div>
          <button type="button" onClick={retry}>Retry</button>
        </div>
      );
    }
    if (loaded !== true && !heatmapDs) {
      return (
        <div className="plx-skel" aria-busy="true" aria-label="Loading liquidity heatmap">
          <div className="ma-skeleton ma-skeleton-row" />
          <div className="ma-skeleton plx-skel__block" />
        </div>
      );
    }
    if (model.empty) {
      return (
        <div className="plx-empty">
          No probed publication in this window — the pool had no profile to sample. Try a wider window.
        </div>
      );
    }
    return (
      <>
        <LiquidityLegend scale={model.scale} isDark={isDark} />
        <ChartCard chartId="plx-heatmap" hideId spec={spec} renderer="canvas" />
        <div className="plx-hint">
          {fmtInt(model.datesSampled)} of {fmtInt(model.datesTotal)} probed dates sampled
          {model.dateStepDays > 1 ? ` (every ${fmtInt(model.dateStepDays)} days)` : ""}, {fmtInt(model.tickStep)}-tick buckets.
          Colour is the tick-weighted mean active liquidity in the bucket. Scroll to zoom.
        </div>
      </>
    );
  })();

  return (
    <MaSection
      title="Liquidity over time"
      meta={<DatasetInfo datasetKey="pool_profile_heatmap" descriptor={descriptor} />}
    >
      <div className="plx-heatmap-window">
        <span className="plx-heatmap-window__label">Window</span>
        <div role="group" aria-label="Heatmap window">
          {HEATMAP_WINDOWS.map((option) => (
            <button
              key={option.id}
              type="button"
              className={window === option.id ? "is-active" : ""}
              onClick={() => setWindow(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <span className="plx-heatmap-window__label">Price axis</span>
        <div role="group" aria-label="Heatmap price axis">
          <button
            type="button"
            className={axisMode === "tick" ? "is-active" : ""}
            title="Absolute tick / price"
            onClick={() => setAxisMode("tick")}
          >
            price
          </button>
          <button
            type="button"
            className={axisMode === "relative" ? "is-active" : ""}
            title="Distance from each date's own current tick — keeps the profile centred when the price trends"
            onClick={() => setAxisMode("relative")}
          >
            % from current
          </button>
        </div>
      </div>
      {body}
    </MaSection>
  );
}
