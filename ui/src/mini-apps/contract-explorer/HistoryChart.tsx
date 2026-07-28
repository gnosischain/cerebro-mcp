import { useEffect, useRef } from "react";
import echarts from "./echarts-line";
import { useTheme } from "../../hooks/useTheme";
import {
  buildHistoryOption,
  isChartable,
  type HistorySeries,
} from "./historyChartOption";

export function HistoryChart({ series }: { series: HistorySeries }) {
  const { isDark } = useTheme();
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const chartable = isChartable(series);

  useEffect(() => {
    if (!chartable || !ref.current) return;
    const chart = echarts.init(
      ref.current,
      isDark ? "cerebro-dark-mini" : "cerebro-light-mini",
      { renderer: "canvas" },
    );
    chartRef.current = chart;
    chart.setOption(buildHistoryOption(series, isDark));

    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
    // Theme changes require a re-init (echarts themes are set at construction).
  }, [series, isDark, chartable]);

  const failed = series.points.length - series.ok_count;

  return (
    <div className="ce-history">
      <div className="ce-history__meta">
        blocks {series.from_block.toLocaleString()}–
        {series.to_block.toLocaleString()} · {series.ok_count}/
        {series.points.length} samples
        {failed > 0 ? ` · ${failed} gap${failed > 1 ? "s" : ""}` : ""}
        {series.decimals ? ` · scaled 1e${series.decimals}` : ""}
      </div>

      {series.warnings.map((w, i) => (
        <div key={i} className="ce-history__warning">
          {w}
        </div>
      ))}

      {chartable ? (
        <div ref={ref} className="ce-history__canvas" />
      ) : (
        <div className="ma-empty">
          No numeric samples to plot
          {series.output_types.length > 1
            ? " — pick an output index for multi-value returns."
            : series.ok_count === 0
              ? " — every sample failed; see the warnings above."
              : " — this function does not return a number."}
        </div>
      )}
    </div>
  );
}
