const sage = "#4a5a3c";
const sageMid = "#7d8a6a";
const sageSoft = "#9ea88e";
const tan = "#c0c4a4";
const sand = "#d6c9a3";
const clay = "#b59569";
const brick = "#a64a3b";

const ink = "#1a1a1a";
const muted = "#8a8275";
const rule = "rgba(31,29,24,0.07)";
const surface = "#ffffff";

const fontMono = "JetBrains Mono, ui-monospace, Menlo, monospace";
const fontDisplay = "Instrument Serif, Iowan Old Style, Georgia, serif";

export const ECHARTS_LIGHT = {
  color: [sage, clay, sageMid, sand, sageSoft, tan, brick],
  backgroundColor: "transparent",
  textStyle: { fontFamily: fontMono, color: muted },
  title: {
    textStyle: { fontFamily: fontDisplay, color: ink, fontWeight: 400 },
  },
  legend: {
    textStyle: { color: muted, fontFamily: fontMono, fontSize: 11 },
  },
  tooltip: {
    backgroundColor: "rgba(255,255,255,0.97)",
    borderColor: rule,
    borderWidth: 1,
    borderRadius: 8,
    extraCssText: "box-shadow:0 4px 16px -8px rgba(31,29,24,0.12);",
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
    axisLabel: { color: muted, fontFamily: fontMono, fontSize: 10 },
    splitLine: {
      lineStyle: { color: rule, type: [2, 3] as unknown as "dashed" },
    },
  },
  line: { lineStyle: { width: 2 }, smooth: true, symbolSize: 0 },
  bar: { itemStyle: { borderRadius: [4, 4, 0, 0] } },
  pie: { itemStyle: { borderColor: surface, borderWidth: 2 } },
};
