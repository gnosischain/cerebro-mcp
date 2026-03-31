const FONT = '"Plus Jakarta Sans", system-ui, sans-serif';

export const ECHARTS_LIGHT = {
  color: [
    "#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6",
    "#3B82F6", "#EC4899", "#14B8A6", "#F97316", "#84CC16",
    "#06B6D4", "#A855F7", "#22C55E", "#FB7185", "#0EA5E9",
  ],
  backgroundColor: "transparent",
  textStyle: { color: "#334155", fontFamily: FONT },
  title: { textStyle: { color: "#0F172A", fontFamily: FONT } },
  legend: { textStyle: { color: "#334155" } },
  tooltip: {
    backgroundColor: "rgba(251,250,247,0.97)",
    borderColor: "rgba(15,23,42,0.08)",
    borderWidth: 1,
    borderRadius: 10,
    extraCssText: "box-shadow:0 4px 12px -4px rgba(15,23,42,0.12);",
    textStyle: { color: "#0F172A", fontFamily: FONT },
  },
  categoryAxis: {
    axisLine: { lineStyle: { color: "rgba(148,163,184,0.3)" } },
    axisLabel: { color: "#94A3B8" },
    splitLine: {
      lineStyle: { color: "rgba(148,163,184,0.15)", type: "dashed" as const },
    },
  },
  valueAxis: {
    axisLine: { lineStyle: { color: "rgba(148,163,184,0.3)" } },
    axisLabel: { color: "#94A3B8" },
    splitLine: {
      lineStyle: { color: "rgba(148,163,184,0.15)", type: "dashed" as const },
    },
  },
};
