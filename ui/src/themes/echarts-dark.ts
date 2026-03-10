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
    backgroundColor: "rgba(30,41,59,0.96)",
    borderColor: "#334155",
    borderWidth: 1,
    borderRadius: 8,
    extraCssText: "box-shadow:0 14px 28px -14px rgba(2,6,23,0.75);",
    textStyle: { color: "#E2E8F0", fontFamily: FONT },
  },
  categoryAxis: {
    axisLine: { lineStyle: { color: "#475569" } },
    axisLabel: { color: "#94A3B8" },
    splitLine: {
      lineStyle: { color: "rgba(148,163,184,0.18)", type: "dashed" as const },
    },
  },
  valueAxis: {
    axisLine: { lineStyle: { color: "#475569" } },
    axisLabel: { color: "#94A3B8" },
    splitLine: {
      lineStyle: { color: "rgba(148,163,184,0.18)", type: "dashed" as const },
    },
  },
};
