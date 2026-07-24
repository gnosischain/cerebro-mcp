// Report LIGHT ECharts theme — editorial "research desk" palette.
// Slot order is fixed (teal, amber, blue, rose, violet, cyan, olive, magenta)
// and validated with the dataviz six-checks validator against the white chart
// surface: lightness band, chroma floor, adjacent-pair CVD separation, and
// >=3:1 mark contrast all pass with no waivers. Do not reorder or eyeball-
// tweak slots — re-run the validator if a hue must change.
const palette = [
  "#0E8C6E", // teal    — lead series (value / USD in most reports)
  "#C0862A", // amber   — counter series (EUR / secondary)
  "#3B66C4", // blue
  "#C2547A", // rose
  "#7C5BD2", // violet
  "#0F86A1", // cyan
  "#6A8A2A", // olive
  "#A84FB0", // magenta
];

const ink = "#14102e";
const muted = "#726c97";
const rule = "rgba(20,16,46,0.10)";
const surface = "#ffffff";

const fontMono = "JetBrains Mono, ui-monospace, Menlo, monospace";

export const ECHARTS_LIGHT = {
  color: palette,
  backgroundColor: "transparent",
  textStyle: { fontFamily: fontMono, color: muted },
  title: {
    textStyle: { fontFamily: fontMono, color: ink, fontWeight: 600 },
  },
  legend: {
    textStyle: { color: muted, fontFamily: fontMono, fontSize: 11 },
    itemWidth: 14,
    itemHeight: 8,
    icon: "roundRect",
    itemGap: 18,
  },
  tooltip: {
    backgroundColor: "rgba(255,255,255,0.97)",
    borderColor: rule,
    borderWidth: 1,
    borderRadius: 4,
    extraCssText: "box-shadow:0 4px 16px -8px rgba(20,16,46,0.12);",
    textStyle: { color: ink, fontFamily: fontMono, fontSize: 12 },
  },
  categoryAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: muted, fontFamily: fontMono, fontSize: 11, hideOverlap: true },
    // valueAxis/logAxis already carry nameTextStyle; categoryAxis was missing it.
    nameTextStyle: { color: muted, fontFamily: fontMono, fontSize: 11 },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: muted, fontFamily: fontMono, fontSize: 11 },
    nameTextStyle: { color: muted, fontFamily: fontMono, fontSize: 11 },
    splitLine: {
      lineStyle: { color: rule, type: [2, 3] as unknown as "dashed" },
    },
  },
  logAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: muted, fontFamily: fontMono, fontSize: 11 },
    nameTextStyle: { color: muted, fontFamily: fontMono, fontSize: 11 },
    splitLine: {
      lineStyle: { color: rule, type: [2, 3] as unknown as "dashed" },
    },
  },
  line: { lineStyle: { width: 2 }, smooth: 0.15, symbolSize: 0, symbol: "none" },
  bar: { itemStyle: { borderRadius: [3, 3, 0, 0] } },
  scatter: { itemStyle: { borderColor: "rgba(255,255,255,0.9)", borderWidth: 1 } },
  pie: { itemStyle: { borderColor: surface, borderWidth: 2 }, label: { color: ink } },
  // On-canvas node/flow labels don't inherit textStyle.color.
  sankey: { label: { color: ink } },
  graph: { label: { color: ink } },
};
