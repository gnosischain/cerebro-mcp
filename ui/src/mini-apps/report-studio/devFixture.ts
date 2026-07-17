// Dev-mode fixture for the Report Studio (plain page = archive with a few
// synthetic entries). Only used when import.meta.env.DEV.

import type { MiniAppPayload } from "../shared/miniAppTypes";
import { APP_ID, type StudioState } from "./types";

const NOW = 1_784_000_000; // fixed epoch for stable snapshots

export function buildMockPayload(): MiniAppPayload<StudioState> {
  return {
    type: "INITIAL_LOAD",
    view_id: "dev-studio",
    app_id: APP_ID,
    title: "Report Studio",
    status: "ready",
    summary_cards: [],
    datasets: {},
    view_state: {
      screen: "archive",
      archive: {
        reports: [
          {
            id: "11111111-1111-4111-8111-111111111111",
            short_id: "11111111",
            kind: "report",
            title_hint: "bridges netflow q2",
            created_utc: NOW - 3600,
            size_kb: 812.4,
            filename:
              "cerebro_report_20260716T090000Z_bridges-netflow-q2_11111111-1111-4111-8111-111111111111.html",
            link: "file:///dev/null",
          },
          {
            id: "22222222-2222-4222-8222-222222222222",
            short_id: "22222222",
            kind: "research",
            title_hint: "circles trust growth",
            created_utc: NOW - 86_400,
            size_kb: 1_204.9,
            filename:
              "cerebro_research_20260715T090000Z_circles-trust-growth_22222222-2222-4222-8222-222222222222.html",
            link: "file:///dev/null",
          },
          {
            id: "33333333-3333-4333-8333-333333333333",
            short_id: "33333333",
            kind: "case_study",
            title_hint: "gnosis pay story",
            created_utc: NOW - 2 * 86_400,
            size_kb: 640.2,
            filename:
              "cerebro_case_study_20260714T090000Z_gnosis-pay-story_33333333-3333-4333-8333-333333333333.html",
            link: "file:///dev/null",
          },
        ],
        total: 3,
        offset: 0,
        limit: 50,
        query: "",
        kind: "",
        sort: "newest",
        warning_count: 0,
      },
      selected_entry: null,
      session_charts: {
        charts: [
          {
            chart_id: "chart_1",
            title: "Daily bridge volume",
            chart_type: "line",
            data_points: 90,
            source_model: "api_bridges_flows_daily",
          },
          {
            chart_id: "chart_2",
            title: "Total TVL",
            chart_type: "numberDisplay",
            data_points: 1,
            source_model: "fct_bridges_kpis_snapshot",
          },
          {
            chart_id: "chart_3",
            title: "Active users",
            chart_type: "numberDisplay",
            data_points: 1,
            source_model: "api_execution_transactions_daily",
          },
        ],
      },
      report_dir: "~/.cerebro/reports",
      mutations_enabled: true,
    },
    provenance: { source: "dev-fixture" },
    warnings: [],
  };
}
