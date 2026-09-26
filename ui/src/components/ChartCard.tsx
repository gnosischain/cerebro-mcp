import { useContext, useEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";

import "../lib/echarts-setup";
import { applyReportPresentation } from "../lib/chartPresentation";
import { getWatermarkGraphic } from "../assets/watermark";
import { useTheme } from "../hooks/useTheme";
import { ChartSurfaceContext } from "./chartSurface";
import { buildDataViewTable, dataViewPalette } from "./dataViewTable";
import { isNumberDisplay, type ChartSpec } from "../types";
import { NumberDisplay } from "./NumberDisplay";
import { ErrorBoundary } from "./ErrorBoundary";

interface Props {
  chartId: string;
  spec: ChartSpec;
  title?: string;
  sql?: string;
  sourceModel?: string;
  /** Hide the CHART_NN id badge (mini-app hosts have no chart numbering). */
  hideId?: boolean;
  /**
   * Fill the parent instead of standing as a card: no head, no border, no
   * margin, `height: 100%`. For a surface where the chart IS the view and the
   * card chrome is pure overhead — the head/foot cost ~70px, which on a
   * full-height graph is the difference between readable and cramped. The
   * caller owns provenance in that case (pass no `sourceModel`/`sql`).
   */
  flush?: boolean;
  /**
   * Override the spec's own `_cerebro_height`. For charts placed in a paired
   * grid row: the card CONTAINERS already stretch to a shared height, but two
   * specs from different builders (620px combo beside a 350px default) leave one
   * chart floating in dead space. The layout knows the row height; the spec
   * builder does not, so the layout gets the last word.
   */
  height?: string;
  onEvents?: Record<string, (params: unknown) => void>;
  /** Called once with the ECharts instance when the chart mounts — for
   * low-level wiring (e.g. zrender clicks + convertFromPixel) not expressible
   * through onEvents. */
  onChartReady?: (chart: unknown) => void;
  /**
   * ECharts renderer. Defaults to canvas (existing behavior for reports and
   * other mini-apps). "svg" renders vector text/marks that stay crisp at any
   * zoom or devicePixelRatio — prefer it for dense analytic charts.
   */
  renderer?: "canvas" | "svg";
}

function chartLabel(chartId: string): string {
  const m = chartId.match(/(\d+)$/);
  if (m) {
    return `CHART_${m[1].padStart(2, "0")}`;
  }
  return chartId.replace(/^chart[_-]?/i, "CHART_").toUpperCase();
}

function ChartCardInner({ chartId, spec, title, sql, sourceModel, hideId, flush = false, height, onEvents, onChartReady, renderer = "canvas" }: Props) {
  const { isDark } = useTheme();
  // Mini-app surfaces (near-black/white paper) need the `-mini` label themes;
  // reports keep the indigo/cream "Terminal" themes.
  const surface = useContext(ChartSurfaceContext);
  const echartsTheme =
    surface === "mini"
      ? isDark
        ? "cerebro-dark-mini"
        : "cerebro-light-mini"
      : isDark
        ? "cerebro-dark"
        : "cerebro-light";
  const [showSql, setShowSql] = useState(false);

  const label = chartLabel(chartId);
  const srcLabel = sourceModel ?? chartId;
  const showFoot = Boolean(sourceModel || sql);

  if (isNumberDisplay(spec)) {
    // KPI cards: drop the dbt source label — the SQL toggle alone is enough.
    return (
      <div id={`chart-${chartId}`} className="chart-card">
        <div className="chart-card-head">
          <div className="chart-card-title">{title || spec.title || ""}</div>
          {!hideId && <div className="chart-card-id">{label}</div>}
        </div>
        <NumberDisplay spec={spec} cardTitle={title || spec.title} />
        {sql && (
          <div className="chart-card-foot chart-card-foot--kpi">
            <span className="spacer" />
            <button
              className="chart-sql-toggle"
              onClick={() => setShowSql(!showSql)}
            >
              {showSql ? "Hide SQL" : "View SQL"}
            </button>
          </div>
        )}
        {showSql && sql && (
          <div className="chart-sql-block">
            <pre>
              <code>{sql}</code>
            </pre>
          </div>
        )}
      </div>
    );
  }

  const echartsOption = useMemo(() => {
    // Reports get the editorial presentation pass (compact axis values,
    // date-aware ticks, humanized names). Mini-app surfaces ship curated
    // specs and are left untouched.
    const base =
      surface === "mini"
        ? (spec as EChartsOption)
        : applyReportPresentation(spec as EChartsOption, title);
    const opt = { ...base };
    opt.graphic = getWatermarkGraphic(isDark);
    opt.animation = true;
    opt.animationDuration = 1000;
    opt.animationEasing = "cubicOut";
    opt.toolbox = {
      show: true,
      right: 16,
      top: 8,
      feature: {
        saveAsImage: { title: "Save as image", pixelRatio: 2 },
        dataView: {
          title: "View data",
          lang: ["Data view", "Close", "Refresh"],
          readOnly: true,
          // The popup panel does NOT inherit the ECharts theme — leave any
          // of these unset and dark mode gets a white panel with the page's
          // light text (unreadable).
          backgroundColor: dataViewPalette(isDark).background,
          textareaColor: dataViewPalette(isDark).textarea,
          textareaBorderColor: dataViewPalette(isDark).textareaBorder,
          textColor: dataViewPalette(isDark).text,
          buttonColor: dataViewPalette(isDark).button,
          buttonTextColor: dataViewPalette(isDark).buttonText,
          optionToContent: (o: unknown) =>
            buildDataViewTable(o as EChartsOption, isDark),
        },
      },
      iconStyle: {
        borderColor: isDark ? "rgba(255,255,255,0.5)" : "rgba(0,0,0,0.4)",
      },
    };
    return opt;
  }, [spec, isDark, surface, title]);

  const chartHeight =
    height
    || ((spec as Record<string, unknown>)?._cerebro_height as string)
    || "350px";

  // Canvas backing stores go stale (→ stretched, fuzzy text) when the card's
  // container resizes without a window resize event (grid reflows, section
  // swaps). echarts-for-react only listens to window resize; observe the
  // container and resize the instance directly (rAF-debounced).
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const echartsRef = useRef<ReactECharts | null>(null);
  useEffect(() => {
    const el = bodyRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        echartsRef.current?.getEchartsInstance()?.resize();
      });
    });
    observer.observe(el);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, []);
  const rendererOpts = useMemo(
    () => ({
      renderer,
      devicePixelRatio:
        typeof window !== "undefined" ? window.devicePixelRatio : undefined,
    }),
    [renderer],
  );

  return (
    <div
      id={`chart-${chartId}`}
      className={flush ? "chart-card chart-card--flush" : "chart-card"}
    >
      {!flush && (
        <div className="chart-card-head">
          <div className="chart-card-title">{title || ""}</div>
          {!hideId && <div className="chart-card-id">{label}</div>}
        </div>
      )}
      <div className="chart-card-body" ref={bodyRef}>
        <ReactECharts
          ref={echartsRef}
          option={echartsOption}
          theme={echartsTheme}
          style={{ width: "100%", height: chartHeight }}
          notMerge
          onEvents={onEvents}
          onChartReady={onChartReady as ((instance: unknown) => void) | undefined}
          opts={rendererOpts}
        />
      </div>
      {showFoot && (
        <div className="chart-card-foot">
          <span className="src">{srcLabel}</span>
          {sql && (
            <button
              className="chart-sql-toggle"
              onClick={() => setShowSql(!showSql)}
            >
              {showSql ? "Hide SQL" : "View SQL"}
            </button>
          )}
        </div>
      )}
      {showSql && sql && (
        <div className="chart-sql-block">
          <pre>
            <code>{sql}</code>
          </pre>
        </div>
      )}
    </div>
  );
}

export function ChartCard(props: Props) {
  return (
    <ErrorBoundary fallbackLabel="Chart">
      <ChartCardInner {...props} />
    </ErrorBoundary>
  );
}
