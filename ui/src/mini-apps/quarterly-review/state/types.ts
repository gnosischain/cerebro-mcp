// Mirrors the python QuarterlyReviewState dict in
// src/cerebro_mcp/tools/quarterly_review.py._default_state.
// Keep field names + shape 1:1.

export type TemplateId =
  | "executive_qbr"
  | "marketing_qbr"
  | "sales_qbr"
  | "product_qbr";

export type FamilyId = "execution" | "tvl_volume" | "bridges" | "consensus";

export type CompareMode =
  | "prior_quarter"
  | "same_quarter_last_year"
  | "trailing_4q_avg";

export type QuarterlyTab =
  | "overview"
  | "compare"
  | "deep_dive"
  | "saved"
  | "publish";

export type AnalysisTemplateId =
  | "cohort_retention"
  | "address_ltv"
  | "churn"
  | "feature_adoption"
  | "segmentation";

export interface SavedAnalysis {
  finding_id: string;
  title: string;
  conclusion: string;
  chart_ids: string[];
  quarter: string;
}

export interface DraftAnalysis {
  title: string;
  conclusion: string;
  chart_ids: string[];
}

export interface PriorityEntry {
  id: string;
  statement: string;
}

export interface ActionItemEntry {
  id: string;
  statement: string;
}

export interface NoteEntry {
  id: string;
  kind: string;
  statement: string;
}

export interface QuarterlyReviewState {
  project_id: string;
  template: TemplateId;
  current_quarter: string;
  compare_quarter: string;
  compare_mode: CompareMode;
  available_quarters: string[];
  active_tab: QuarterlyTab;
  kpi_families: FamilyId[];
  selected_family: FamilyId;
  filters: Record<string, string>;
  saved_analyses: SavedAnalysis[];
  draft_analysis: DraftAnalysis;
  priorities: PriorityEntry[];
  action_items: ActionItemEntry[];
  notes: NoteEntry[];
  status_message: string;
}
