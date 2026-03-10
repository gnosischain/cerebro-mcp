import type { EChartsOption } from "echarts";

export interface NumberDisplaySpec {
  type: "numberDisplay";
  title: string;
  value: number | string;
  format?: string;
}

export type ChartSpec = EChartsOption | NumberDisplaySpec;

export interface ReportData {
  title: string;
  timestamp: string;
  charts: Record<string, ChartSpec>;
  sections_html: string;
}

export interface HtmlSection {
  title: string;
  html: string;
}

export function isNumberDisplay(spec: ChartSpec): spec is NumberDisplaySpec {
  return (
    typeof spec === "object" &&
    spec !== null &&
    "type" in spec &&
    (spec as NumberDisplaySpec).type === "numberDisplay"
  );
}
