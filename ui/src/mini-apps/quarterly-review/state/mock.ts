// Dev-only mock payload — hydrated in quarterly-review-main.tsx when
// import.meta.env.DEV is true and ?demo=loaded is set. Production builds
// never reference this file at runtime (tree-shaken via DEV conditional).

import type {
  DatasetDescriptor,
  MiniAppPayload,
} from "../../shared/miniAppTypes";
import type { QuarterlyReviewState } from "./types";

const APP_ID = "quarterly_review";

const ds = (
  key: string,
  columns: string[],
  types: string[],
  rows: unknown[][],
): DatasetDescriptor => ({
  key,
  title: key,
  sql: `SELECT * FROM dbt.api_* -- ${key}`,
  database: "dbt",
  columns: columns.map((name, i) => ({ name, type: types[i] ?? "Unknown" })),
  stats: {
    row_count: rows.length,
    rows_returned: rows.length,
    mode: "exact_bounded",
    warnings: [],
  },
  preview_rows: rows,
});

const KPI_COLS = ["metric", "current", "prior", "delta_pct"];
const KPI_TYPES = ["String", "Float64", "Float64", "Float64"];

const TREND_COLS = ["day", "quarter", "value"];
const TREND_TYPES = ["Date", "String", "Float64"];

const BREAK_COLS = ["bucket", "value"];
const BREAK_TYPES = ["String", "Float64"];

const SCATTER_COLS = ["day", "x", "y", "quarter"];
const SCATTER_TYPES = ["Date", "Float64", "Float64", "String"];

function trendRows(q: string, c: string, seedQ: number, seedC: number): unknown[][] {
  const days = 14;
  const out: unknown[][] = [];
  for (let i = 0; i < days; i++) {
    out.push([
      `2025-12-${String(18 + i).padStart(2, "0")}`,
      c,
      seedC + Math.sin(i) * seedC * 0.08,
    ]);
  }
  for (let i = 0; i < days; i++) {
    out.push([
      `2026-01-${String(10 + i).padStart(2, "0")}`,
      q,
      seedQ + Math.cos(i) * seedQ * 0.08,
    ]);
  }
  return out;
}

export function buildMockPayload(): MiniAppPayload<QuarterlyReviewState> {
  const quarter = "2026-Q1";
  const compare = "2025-Q4";
  const families = ["execution", "tvl_volume", "bridges", "consensus"] as const;

  const datasets: Record<string, DatasetDescriptor> = {
    kpi_execution_qoq: ds("kpi_execution_qoq", KPI_COLS, KPI_TYPES, [
      ["tx_count", 12_430_000, 11_100_000, 0.12],
      ["dau_avg", 58_200, 54_000, 0.078],
    ]),
    kpi_tvl_volume_qoq: ds("kpi_tvl_volume_qoq", KPI_COLS, KPI_TYPES, [
      ["tvl_avg", 420_000_000, 380_000_000, 0.105],
      ["volume_sum", 1_250_000_000, 1_100_000_000, 0.136],
    ]),
    kpi_bridges_qoq: ds("kpi_bridges_qoq", KPI_COLS, KPI_TYPES, [
      ["bridge_volume_in", 280_000_000, 310_000_000, -0.097],
      ["bridge_volume_out", 265_000_000, 290_000_000, -0.086],
    ]),
    kpi_consensus_qoq: ds("kpi_consensus_qoq", KPI_COLS, KPI_TYPES, [
      ["active_validators_avg", 162_400, 158_900, 0.022],
      ["staked_avg", 2_120_000, 2_050_000, 0.034],
    ]),
    trend_execution: ds(
      "trend_execution",
      TREND_COLS,
      TREND_TYPES,
      trendRows(quarter, compare, 420_000, 380_000),
    ),
    trend_tvl_volume: ds(
      "trend_tvl_volume",
      TREND_COLS,
      TREND_TYPES,
      trendRows(quarter, compare, 420_000_000, 380_000_000),
    ),
    trend_bridges: ds(
      "trend_bridges",
      TREND_COLS,
      TREND_TYPES,
      trendRows(quarter, compare, 9_200_000, 10_100_000),
    ),
    trend_consensus: ds(
      "trend_consensus",
      TREND_COLS,
      TREND_TYPES,
      trendRows(quarter, compare, 162_400, 158_900),
    ),
    breakdown_bridges: ds("breakdown_bridges", BREAK_COLS, BREAK_TYPES, [
      ["xDAI Bridge", 180_000_000],
      ["OmniBridge", 72_000_000],
      ["Hashi", 18_000_000],
      ["LayerZero", 6_400_000],
      ["Other", 3_600_000],
    ]),
    scatter_execution: ds(
      "scatter_execution",
      SCATTER_COLS,
      SCATTER_TYPES,
      Array.from({ length: 20 }, (_, i) => [
        `2026-01-${String(i + 1).padStart(2, "0")}`,
        400_000 + Math.random() * 40_000,
        55_000 + Math.random() * 8_000,
        quarter,
      ]),
    ),
  };

  // Dev-only: allow `?tab=compare|deep_dive|saved|publish` so visual
  // verification can jump straight to a specific tab without server PATCH.
  const tabParam =
    typeof window !== "undefined"
      ? new URLSearchParams(window.location.search).get("tab")
      : null;
  const initialTab: QuarterlyReviewState["active_tab"] =
    tabParam === "compare" ||
    tabParam === "deep_dive" ||
    tabParam === "saved" ||
    tabParam === "publish"
      ? tabParam
      : "overview";

  const state: QuarterlyReviewState = {
    project_id: "rp_demo1234567",
    template: "executive_qbr",
    current_quarter: quarter,
    compare_quarter: compare,
    compare_mode: "prior_quarter",
    available_quarters: [
      "2026-Q1",
      "2025-Q4",
      "2025-Q3",
      "2025-Q2",
      "2025-Q1",
      "2024-Q4",
    ],
    active_tab: initialTab,
    kpi_families: [...families],
    selected_family: "execution",
    filters: {},
    saved_analyses: [
      {
        finding_id: "fd_demo1",
        title: "Tx volume accelerated 12% Q-o-Q",
        conclusion:
          "Transaction count rose 12% vs Q4 2025, led by mid-quarter surge in xDAI bridge activity.",
        chart_ids: ["chart_1"],
        quarter,
      },
    ],
    draft_analysis: { title: "", conclusion: "", chart_ids: [] },
    priorities: [
      { id: "m_1", statement: "Ship Hashi bridge UX improvements in Q2" },
    ],
    action_items: [
      { id: "m_2", statement: "Audit LP incentive budget [owner=alice, due=2026-05-15]" },
    ],
    notes: [
      {
        id: "m_3",
        kind: "observation",
        statement: "Validator count growth decelerated late March.",
      },
    ],
    status_message: "1 analysis saved · try the Publish tab",
  };

  return {
    type: "INITIAL_LOAD",
    view_id: "dev-view",
    app_id: APP_ID,
    title: `Quarterly Review — ${quarter}`,
    status: "ready",
    summary_cards: [
      {
        label: "execution · tx_count",
        value: "12.43M",
        delta: "+12.0%",
        tone: "positive",
      },
      {
        label: "tvl_volume · volume_sum",
        value: "1.25B",
        delta: "+13.6%",
        tone: "positive",
      },
      {
        label: "bridges · bridge_volume_in",
        value: "280M",
        delta: "-9.7%",
        tone: "negative",
      },
      {
        label: "consensus · active_validators_avg",
        value: "162k",
        delta: "+2.2%",
        tone: "warning",
      },
    ],
    datasets,
    view_state: state,
    provenance: {
      project_id: state.project_id,
      template: state.template,
      quarter: state.current_quarter,
      compare: state.compare_quarter,
    },
    warnings: [],
  };
}
