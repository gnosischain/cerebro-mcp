import type { EChartsOption } from "echarts";

export interface NumberDisplayChangeSpec {
  value: number | string;
  format?: string;
  direction?: "positive" | "negative" | "neutral";
  label?: string;
}

export interface NumberDisplaySpec {
  type: "numberDisplay";
  title: string;
  value: number | string;
  format?: string;
  change?: NumberDisplayChangeSpec;
}

export type ChartSpec = EChartsOption | NumberDisplaySpec;

export interface QueryInfo {
  sql: string;
  database: string;
  title: string;
  source?: "semantic" | "raw";
}

export interface ResearchFootnote {
  id: string;
  text: string;
}

export interface ResearchMetadata {
  deck: string;
  authors?: string[];
  published_date?: string;
  category?: string | null;
  key_takeaways?: string[];
  footnotes?: ResearchFootnote[];
  reading_minutes?: number;
}

export interface ReportData {
  title: string;
  timestamp: string;
  charts: Record<string, ChartSpec>;
  sections_html: string;
  queries?: Record<string, QueryInfo>;
  file_uri?: string;
  analysis_path?: string;
  presentation_mode?: "report" | "visual_answer" | "research";
  research_metadata?: ResearchMetadata;
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
