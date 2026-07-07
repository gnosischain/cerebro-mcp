// Gnosis "Terminal" — DARK ECharts theme.
// Series order: index 0 = line (lime), 1 = bar (violet), full array = donut.
const palette = ["#B4F03C", "#7B61FF", "#FF7A9C", "#4DD0E1", "#C6A6FF", "#F5B14C"];

const ink = "#EDE8FA";
// Axis / legend labels need higher contrast on the deep-indigo surface than the
// `--text-muted` token (#7E77AD) gives — it anti-aliases to a soft fringe on
// near-black. Promote a step toward `--text-secondary`.
const muted = "#8b84b5";
const rule = "rgba(200,194,232,0.12)";
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
    axisLabel: { color: muted, fontFamily: fontMono, fontSize: 10 },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: muted, fontFamily: fontMono, fontSize: 10 },
    splitLine: {
      lineStyle: { color: rule, type: [2, 3] as unknown as "dashed" },
    },
  },
  line: { lineStyle: { width: 2.4 }, smooth: true, symbolSize: 0 },
  bar: { itemStyle: { borderRadius: [3, 3, 0, 0] } },
  pie: { itemStyle: { borderColor: surface, borderWidth: 2 } },
};
