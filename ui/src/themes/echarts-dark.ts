const FONT = '"Plus Jakarta Sans", system-ui, sans-serif';

export const ECHARTS_DARK = {
  color: [
    "#818CF8", "#34D399", "#FBBF24", "#F87171", "#A78BFA",
    "#60A5FA", "#F472B6", "#2DD4BF", "#FDBA74", "#A3E635",
    "#67E8F9", "#C4B5FD", "#4ADE80", "#FDA4AF", "#38BDF8",
  ],
  backgroundColor: "transparent",
  textStyle: { color: "#E2E8F0", fontFamily: FONT },
  title: { textStyle: { color: "#E2E8F0", fontFamily: FONT } },
  legend: { textStyle: { color: "#CBD5E1" } },
  tooltip: {
    backgroundColor: "rgba(21,28,36,0.97)",
    borderColor: "rgba(226,232,240,0.08)",
    borderWidth: 1,
    borderRadius: 6,
    extraCssText: "box-shadow:0 4px 12px -4px rgba(0,0,0,0.3);",
    textStyle: { color: "#E2E8F0", fontFamily: FONT },
  },
  categoryAxis: {
    axisLine: { lineStyle: { color: "rgba(148,163,184,0.2)" } },
    axisLabel: { color: "#64748B" },
    splitLine: {
      lineStyle: { color: "rgba(148,163,184,0.1)", type: "dashed" as const },
    },
  },
  valueAxis: {
    axisLine: { lineStyle: { color: "rgba(148,163,184,0.2)" } },
    axisLabel: { color: "#64748B" },
    splitLine: {
      lineStyle: { color: "rgba(148,163,184,0.1)", type: "dashed" as const },
    },
  },
};
