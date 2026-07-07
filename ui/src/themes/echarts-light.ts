// Gnosis "Terminal" — LIGHT ECharts theme.
// Series order: index 0 = line, 1 = bar, full array = donut. Lime darkens to
// #5E7A0A so it holds contrast on the cream canvas.
const palette = ["#5E7A0A", "#5B44E0", "#D64B6A", "#1E9AA8", "#8A6DEF", "#C08A2E"];

const ink = "#14102e";
const muted = "#726c97";
const rule = "rgba(20,16,46,0.12)";
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
