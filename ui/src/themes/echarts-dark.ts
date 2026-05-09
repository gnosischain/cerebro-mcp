const sage = "#9ec081";
const sageMid = "#b5d195";
const sageSoft = "#7d9966";
const tan = "#c9b487";
const sand = "#d4b483";
const clay = "#d4906a";
const brick = "#d97565";

const ink = "#f1ece2";
// Axis / legend labels need higher contrast on the dark surface than the
// `--text-muted` token gives — that color anti-aliases to a soft, blurry
// fringe on near-black. Promote labels to `--text-secondary`.
const labelInk = "#c9c2b3";
const rule = "rgba(241,236,226,0.07)";
const surface = "#1c1a15";

const fontMono = "JetBrains Mono, ui-monospace, Menlo, monospace";
const fontDisplay = "Instrument Serif, Iowan Old Style, Georgia, serif";

export const ECHARTS_DARK = {
  color: [sage, clay, sageMid, sand, sageSoft, tan, brick],
  backgroundColor: "transparent",
  textStyle: { fontFamily: fontMono, color: labelInk },
  title: {
    textStyle: { fontFamily: fontDisplay, color: ink, fontWeight: 400 },
  },
  legend: {
    textStyle: { color: labelInk, fontFamily: fontMono, fontSize: 11 },
  },
  tooltip: {
    backgroundColor: "rgba(28,26,21,0.97)",
    borderColor: rule,
    borderWidth: 1,
    borderRadius: 8,
    extraCssText: "box-shadow:0 6px 24px -10px rgba(0,0,0,0.5);",
    textStyle: { color: ink, fontFamily: fontMono, fontSize: 12 },
  },
  categoryAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: labelInk, fontFamily: fontMono, fontSize: 10 },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false },
    axisLabel: { color: labelInk, fontFamily: fontMono, fontSize: 10 },
    splitLine: {
      lineStyle: { color: rule, type: [2, 3] as unknown as "dashed" },
    },
  },
  line: { lineStyle: { width: 2 }, smooth: true, symbolSize: 0 },
  bar: { itemStyle: { borderRadius: [4, 4, 0, 0] } },
  pie: { itemStyle: { borderColor: surface, borderWidth: 2 } },
};
