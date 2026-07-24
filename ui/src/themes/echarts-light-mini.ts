// Mini-app LIGHT ECharts theme — for charts inside `.mini-app-scope` (white
// paper surfaces), selected via ChartSurfaceContext. Reports keep the cream
// `echarts-light.ts` theme untouched.
//
// ECharts cannot read CSS variables, so each value below mirrors a token from
// `themes/tokens.css:147-231` (the `[data-theme="light"] .mini-app-scope`
// block) and is commented with the token it shadows.
//
// Series palette is IDENTICAL to the report light theme (user decision —
// this theme fixes labels only).
const palette = ["#5E7A0A", "#5B44E0", "#D64B6A", "#1E9AA8", "#8A6DEF", "#C08A2E"];

const ink = "#111418"; // --text-primary (tokens.css:200)
const muted = "#5b6473"; // --text-muted (tokens.css:202)
const rule = "rgba(15,23,42,0.10)"; // --section-divider (tokens.css:205)
const surface = "#ffffff"; // --surface (tokens.css:192)

// Axis/legend text moves OFF JetBrains Mono to match the dark-mini theme.
const fontBody = "Inter, system-ui, -apple-system, sans-serif";

export const ECHARTS_LIGHT_MINI = {
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
    backgroundColor: "rgba(255,255,255,0.97)", // --surface (tokens.css:192) at 97%
    borderColor: "rgba(15,23,42,0.18)", // --border (tokens.css:203)
    borderWidth: 1,
    borderRadius: 4,
    extraCssText: "box-shadow:0 4px 16px -8px rgba(15,23,42,0.12);",
    textStyle: { color: ink, fontFamily: fontBody, fontSize: 12 },
  },
  categoryAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: muted, fontFamily: fontBody, fontSize: 11, fontWeight: 400 },
    // Axis NAME labels otherwise fall back to ECharts' default #333; mirror the
    // dark-mini fix so both surfaces theme axis names consistently.
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
    // Mirror the dark-mini fix: theme on-bar data labels so they match the
    // axis text weight/color instead of ECharts' heavier default.
    label: { color: muted, fontFamily: fontBody, fontWeight: 400 },
  },
  pie: { itemStyle: { borderColor: surface, borderWidth: 2 }, label: { color: ink } },
  sankey: { label: { color: ink } },
  graph: { label: { color: ink } },
};
