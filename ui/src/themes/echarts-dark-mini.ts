// Mini-app DARK ECharts theme — for charts inside `.mini-app-scope` (cool
// near-black surfaces), selected via ChartSurfaceContext. Reports keep the
// indigo `echarts-dark.ts` theme untouched.
//
// ECharts cannot read CSS variables, so each value below mirrors a token from
// `themes/tokens.css:147-231` (the `.mini-app-scope` block) and is commented
// with the token it shadows. Keep them in sync when tokens change.
//
// Series palette is IDENTICAL to the report dark theme (user decision —
// this theme fixes labels only).
const palette = ["#B4F03C", "#7B61FF", "#FF7A9C", "#4DD0E1", "#C6A6FF", "#F5B14C"];

const ink = "#e6e9ee"; // --text-primary (tokens.css:158)
const muted = "#aab3be"; // --text-secondary (tokens.css:159) — replaces the indigo-tuned #8b84b5
const rule = "rgba(255,255,255,0.10)"; // --section-divider (tokens.css:163)
const surface = "#12161c"; // --surface (tokens.css:150)

// Axis/legend text moves OFF JetBrains Mono: 10px mono halation on the
// near-black surface is the perceived "bold labels" bug.
const fontBody = "Inter, system-ui, -apple-system, sans-serif";

export const ECHARTS_DARK_MINI = {
  color: palette,
  backgroundColor: "transparent",
  textStyle: { fontFamily: fontBody, color: muted },
  title: {
    textStyle: { color: ink, fontFamily: fontBody, fontWeight: 600 },
  },
  legend: {
    textStyle: { color: muted, fontFamily: fontBody, fontSize: 11 },
  },
  tooltip: {
    backgroundColor: "rgba(26,31,38,0.97)", // --surface-2 (tokens.css:152) at 97%
    borderColor: "rgba(255,255,255,0.12)", // --border (tokens.css:161)
    borderWidth: 1,
    borderRadius: 4,
    extraCssText: "box-shadow:0 6px 24px -10px rgba(0,0,0,0.6);",
    textStyle: { color: ink, fontFamily: fontBody, fontSize: 12 },
  },
  categoryAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: muted, fontFamily: fontBody, fontSize: 11, fontWeight: 400 },
    // Axis NAME labels (e.g. "cum. WETH", "WETH per USDC", "quote / base")
    // otherwise fall back to ECharts' default #333 — invisible on the
    // near-black mini surface. This is the main dark-mode readability fix.
    nameTextStyle: { color: muted, fontFamily: fontBody, fontSize: 11 },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: muted, fontFamily: fontBody, fontSize: 11, fontWeight: 400 },
    nameTextStyle: { color: muted, fontFamily: fontBody, fontSize: 11 },
    splitLine: {
      lineStyle: { color: rule, type: [2, 3] as unknown as "dashed" },
    },
  },
  // Mirror valueAxis so log-scale mini charts keep themed labels (the report
  // themes already carry this block; the mini themes were missing it).
  logAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: muted, fontFamily: fontBody, fontSize: 11, fontWeight: 400 },
    nameTextStyle: { color: muted, fontFamily: fontBody, fontSize: 11 },
    splitLine: {
      lineStyle: { color: rule, type: [2, 3] as unknown as "dashed" },
    },
  },
  line: { lineStyle: { width: 2.4 }, smooth: true, symbolSize: 0 },
  bar: {
    itemStyle: { borderRadius: [3, 3, 0, 0] },
    // On-bar data labels (e.g. concentration percent tops) otherwise render
    // heavier/whiter than the themed axis text. Match axis color/weight/font
    // so they stay clean on the near-black surface.
    label: { color: muted, fontFamily: fontBody, fontWeight: 400 },
  },
  // On-canvas labels don't inherit textStyle.color; theme them so pie/sankey/
  // graph text stays legible on the mini surface.
  pie: { itemStyle: { borderColor: surface, borderWidth: 2 }, label: { color: ink } },
  sankey: { label: { color: ink } },
  graph: { label: { color: ink } },
};
