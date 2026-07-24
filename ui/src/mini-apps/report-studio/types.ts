// Template Gallery types — mirror of the view_state built by
// src/cerebro_mcp/tools/visualization/report_studio.py. New backend fields
// stay optional so the frontend never requires a lockstep deploy.
//
// The template catalog itself is compile-time data (model/catalog.ts) and does
// NOT travel through view_state.

import type { ChartSpec, QueryInfo } from "../../types";

export const APP_ID = "report_studio";

export type ReportKind = "report" | "research" | "case_study";

export interface ArchiveEntry {
  id: string;
  short_id: string;
  kind: ReportKind;
  /** Lossy 3-word filename hint — NOT the full title (that arrives with the
   * preview payload). Gallery search matches slug/filename/id only. */
  title_hint: string;
  created_utc: number;
  size_kb: number;
  filename: string;
  link: string;
}

export interface ArchivePage {
  reports: ArchiveEntry[];
  total: number;
  offset: number;
  limit: number;
  query: string;
  kind: string;
  sort: string;
  /** Files skipped due to stat/read errors (int on every path). */
  warning_count: number;
}

export interface ReportEntry {
  ok: boolean;
  error?: string;
  candidates?: string[];
  id: string;
  kind: ReportKind;
  title: string;
  subtitle?: string;
  timestamp?: string;
  presentation_mode?: string;
  charts: Record<string, ChartSpec>;
  sections_html: string;
  queries?: Record<string, QueryInfo>;
  file: {
    path: string;
    filename: string;
    size_kb: number;
    created_utc: number;
    link: string;
  };
}

export interface StudioState {
  screen: "archive" | "preview";
  archive: ArchivePage;
  selected_entry: ReportEntry | null;
  report_dir: string;
  mutations_enabled: boolean;
}
