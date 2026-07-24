// Report DARK ECharts theme — editorial "research desk" palette.
// Slot order is fixed (teal, amber, blue, rose, violet, cyan, olive, magenta)
// and validated with the dataviz six-checks validator against the indigo
// chart surface (#16123a): lightness band, chroma floor, adjacent-pair CVD
// separation, and >=3:1 mark contrast all pass. Do not reorder or eyeball-
// tweak slots — re-run the validator if a hue must change.
const palette = [
  "#21A87F", // teal    — lead series (value / USD in most reports)
  "#B5891F", // amber   — counter series (EUR / secondary)
  "#6E8FE8", // blue
  "#D06180", // rose
  "#9C7BE8", // violet
  "#2FA0B8", // cyan
  "#74A03C", // olive
  "#BA62BE", // magenta
];

const ink = "#EDE8FA";
// Axis / legend labels need higher contrast on the deep-indigo surface than the
// `--text-muted` token (#7E77AD) gives — it anti-aliases to a soft fringe on
// near-black. Promote a step toward `--text-secondary`.
const muted = "#8b84b5";
const rule = "rgba(200,194,232,0.10)";
const surface = "#16123a";

const fontMono = "JetBrains Mono, ui-monospace, Menlo, monospace";

export const ECHARTS_DARK = {
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
    backgroundColor: "rgba(29,24,72,0.97)",
    borderColor: rule,
    borderWidth: 1,
    borderRadius: 4,
    extraCssText: "box-shadow:0 6px 24px -10px rgba(0,0,0,0.6);",
    textStyle: { color: ink, fontFamily: fontMono, fontSize: 12 },
  },
  categoryAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: muted, fontFamily: fontMono, fontSize: 11, hideOverlap: true },
    // valueAxis/logAxis already carry nameTextStyle; categoryAxis was missing it
    // so category-axis names fell back to the default dark color.
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
  scatter: { itemStyle: { borderColor: "rgba(237,232,250,0.85)", borderWidth: 1 } },
  pie: { itemStyle: { borderColor: surface, borderWidth: 2 }, label: { color: ink } },
  // On-canvas node/flow labels don't inherit textStyle.color.
  sankey: { label: { color: ink } },
  graph: { label: { color: ink } },
};
