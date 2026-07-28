// Pure option builder for the history line chart. Kept out of the component
// so the shape (gaps, zoom mode, axis typing) is unit-testable without a DOM.

export interface HistoryPoint {
  block: number;
  timestamp: number | null;
  status: string;
  value: unknown;
  value_float: number | null;
  error: string;
}

export interface HistorySeries {
  signature: string;
  range_label: string;
  from_block: number;
  to_block: number;
  output_index: number;
  decimals: number | null;
  output_types: string[];
  points: HistoryPoint[];
  ok_count: number;
  truncated: boolean;
  warnings: string[];
  swept_at: string;
}

/** A series is only plottable if some sample decoded to a number. */
export function isChartable(series: HistorySeries): boolean {
  return series.points.some(
    (p) => p.status === "ok" && typeof p.value_float === "number",
  );
}

export function buildHistoryOption(
  series: HistorySeries,
  isDark: boolean,
): Record<string, unknown> {
  // Failed samples become null, which ECharts renders as a gap. Plotting them
  // as 0 would invent a value the chain never had.
  const data = series.points.map((p) => [
    p.timestamp ? p.timestamp * 1000 : null,
    p.status === "ok" ? p.value_float : null,
  ]);

  const blockByTime = new Map<number, number>();
  for (const p of series.points) {
    if (p.timestamp) blockByTime.set(p.timestamp * 1000, p.block);
  }

  return {
    animation: false,
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      formatter: (params: unknown) => {
        const rows = Array.isArray(params) ? params : [params];
        const first = rows[0] as { value?: [number, number | null] } | undefined;
        if (!first?.value) return "";
        const [ms, value] = first.value;
        const block = blockByTime.get(ms);
        const when = new Date(ms).toISOString().replace("T", " ").slice(0, 16);
        const shown = value === null ? "no data" : String(value);
        return [
          `${when} UTC`,
          block !== undefined ? `block ${block.toLocaleString()}` : "",
          `<b>${shown}</b>`,
        ]
          .filter(Boolean)
          .join("<br/>");
      },
    },
    xAxis: { type: "time" },
    yAxis: { type: "value", scale: true },
    // Inside-only zoom: wheel/pinch. No slider bars anywhere in this codebase.
    dataZoom: [{ type: "inside", throttle: 50 }],
    series: [
      {
        type: "line",
        name: series.signature,
        data,
        showSymbol: series.points.length <= 60,
        symbolSize: 4,
        smooth: false,
        connectNulls: false,
        lineStyle: { width: 1.5 },
      },
    ],
    textStyle: { fontFamily: "var(--font-mono)" },
    backgroundColor: "transparent",
    darkMode: isDark,
  };
}
