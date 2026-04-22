import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import { useMiniApp } from "../shared/useMiniApp";
import { WarningBanner } from "../shared/WarningBanner";
import { SummaryCards } from "../shared/SummaryCards";
import { DatasetTable } from "../shared/DatasetTable";
import { SegmentedControl } from "../shared/SegmentedControl";
import { AsyncButton } from "../shared/AsyncButton";
import type { DatasetDescriptor, MiniAppPayload } from "../shared/miniAppTypes";

type OpportunityType = "LP" | "Lending";
type YieldTab = "Overview" | "History" | "Compare" | "Simulation";
type SortKey =
  | "headline_rate_desc"
  | "headline_rate_asc"
  | "tvl_desc"
  | "fees_7d_desc"
  | "volume_7d_desc"
  | "utilization_desc";

interface YieldFilters {
  token: string;
  type: string;
  protocol: string;
}

interface SimulationPoint {
  date: string;
  value_usd: number;
  gain_usd: number;
}

interface SimulationResult {
  mode: "forward" | "historical_replay";
  principal_usd: number;
  compound: boolean;
  start_date: string;
  end_date: string;
  ending_value_usd: number;
  gain_usd: number;
  return_pct: number;
  annualized_return_pct: number;
  series: SimulationPoint[];
}

interface YieldOpportunitiesState {
  query: string;
  filters: YieldFilters;
  sort: SortKey;
  active_tab: YieldTab;
  selected_opportunity_key: string;
  compare_with: string;
  loaded_detail_keys: string[];
  simulation?: SimulationResult | null;
  mobile_panel: string;
}

interface OpportunityRow {
  opportunity_key: string;
  type: OpportunityType;
  token: string;
  name: string;
  address: string;
  pool_key?: string | null;
  protocol: string;
  yield_apr?: number | null;
  yield_apy?: number | null;
  borrow_apy?: number | null;
  tvl?: number | null;
  total_supplied?: number | null;
  total_borrowed?: number | null;
  fees_7d?: number | null;
  volume_usd_7d?: number | null;
  net_apr_7d?: number | null;
  utilization_rate?: number | null;
  fee_pct?: number | null;
  rate_trend_14d?: number[] | null;
  reserve_address?: string | null;
  headline_rate?: number | null;
  lvr_apr_7d?: number | null;
}

const APP_ID = "yield_opportunities";

const MOCK_PAYLOAD: MiniAppPayload<YieldOpportunitiesState> = {
  type: "INITIAL_LOAD",
  view_id: "dev-view",
  app_id: APP_ID,
  title: "Yield Opportunities",
  status: "ready",
  summary_cards: [
    { label: "Opportunities", value: "2", tone: "neutral" },
    { label: "LP / lending", value: "1 / 1", tone: "neutral" },
  ],
  datasets: {
    opportunities: {
      key: "opportunities",
      title: "Ranked opportunities",
      sql: "",
      database: "dbt",
      columns: [
        { name: "opportunity_key", type: "str" },
        { name: "type", type: "str" },
        { name: "token", type: "str" },
        { name: "name", type: "str" },
        { name: "protocol", type: "str" },
        { name: "headline_rate", type: "float" },
        { name: "tvl", type: "float" },
      ],
      preview_rows: [
        ["lp:uniswap v3:0x1111111111111111111111111111111111111111", "LP", "GNO", "GNO/xDAI 0.3%", "Uniswap V3", 8.2, 2_500_000],
        ["lending:aave:0x2222222222222222222222222222222222222222", "Lending", "sDAI", "sDAI", "Aave", 5.4, null],
      ],
      stats: { row_count: 2, rows_returned: 2, mode: "exact_bounded", warnings: [] },
    },
  },
  view_state: {
    query: "",
    filters: { token: "", type: "", protocol: "" },
    sort: "headline_rate_desc",
    active_tab: "Overview",
    selected_opportunity_key: "",
    compare_with: "",
    loaded_detail_keys: [],
    simulation: null,
    mobile_panel: "ranking",
  },
  warnings: [],
};

function toNumber(value: unknown): number {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function formatNumber(value: number, digits = 2): string {
  if (!Number.isFinite(value)) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function formatMoney(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return `$${value.toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
}

function mapRows(dataset?: DatasetDescriptor): Record<string, unknown>[] {
  if (!dataset) return [];
  return dataset.preview_rows.map((row) =>
    Object.fromEntries(dataset.columns.map((column, index) => [column.name, row[index]])),
  );
}

function normalizeOpportunityRows(dataset?: DatasetDescriptor): OpportunityRow[] {
  return mapRows(dataset).map((row) => ({
    opportunity_key: String(row.opportunity_key ?? ""),
    type: String(row.type ?? "LP") as OpportunityType,
    token: String(row.token ?? ""),
    name: String(row.name ?? row.token ?? ""),
    address: String(row.address ?? ""),
    pool_key: row.pool_key ? String(row.pool_key) : null,
    protocol: String(row.protocol ?? ""),
    yield_apr: row.yield_apr === undefined ? null : toNumber(row.yield_apr),
    yield_apy: row.yield_apy === undefined ? null : toNumber(row.yield_apy),
    borrow_apy: row.borrow_apy === undefined ? null : toNumber(row.borrow_apy),
    tvl: row.tvl === undefined ? null : toNumber(row.tvl),
    total_supplied: row.total_supplied === undefined ? null : toNumber(row.total_supplied),
    total_borrowed: row.total_borrowed === undefined ? null : toNumber(row.total_borrowed),
    fees_7d: row.fees_7d === undefined ? null : toNumber(row.fees_7d),
    volume_usd_7d: row.volume_usd_7d === undefined ? null : toNumber(row.volume_usd_7d),
    net_apr_7d: row.net_apr_7d === undefined ? null : toNumber(row.net_apr_7d),
    utilization_rate: row.utilization_rate === undefined ? null : toNumber(row.utilization_rate),
    fee_pct: row.fee_pct === undefined ? null : toNumber(row.fee_pct),
    rate_trend_14d: Array.isArray(row.rate_trend_14d)
      ? row.rate_trend_14d.map((point) => toNumber(point))
      : null,
    reserve_address: row.reserve_address ? String(row.reserve_address) : null,
    headline_rate: row.headline_rate === undefined ? null : toNumber(row.headline_rate),
    lvr_apr_7d: row.lvr_apr_7d === undefined ? null : toNumber(row.lvr_apr_7d),
  }));
}

function sortOpportunities(rows: OpportunityRow[], sort: SortKey): OpportunityRow[] {
  const sorted = [...rows];
  sorted.sort((left, right) => {
    const get = (row: OpportunityRow): number => {
      switch (sort) {
        case "headline_rate_desc":
        case "headline_rate_asc":
          return row.headline_rate ?? 0;
        case "tvl_desc":
          return row.tvl ?? row.total_supplied ?? 0;
        case "fees_7d_desc":
          return row.fees_7d ?? 0;
        case "volume_7d_desc":
          return row.volume_usd_7d ?? 0;
        case "utilization_desc":
          return row.utilization_rate ?? 0;
      }
    };
    const leftValue = get(left);
    const rightValue = get(right);
    return sort === "headline_rate_asc" ? leftValue - rightValue : rightValue - leftValue;
  });
  return sorted;
}

function historyRows(dataset?: DatasetDescriptor): Record<string, unknown>[] {
  return mapRows(dataset).sort((left, right) => String(left.date ?? "").localeCompare(String(right.date ?? "")));
}

function buildHistoryOption(rows: Record<string, unknown>[], type: OpportunityType, isDark: boolean) {
  if (!rows.length) {
    return { title: { text: "No history available", left: "center", top: "center" } };
  }
  const dates = rows.map((row) => String(row.date ?? ""));
  const primarySeries =
    type === "LP"
      ? [
          { name: "Net APR", values: rows.map((row) => toNumber(row.net_apr_7d)) },
          { name: "Fee APR", values: rows.map((row) => toNumber(row.fee_apr_7d)) },
          { name: "LVR APR", values: rows.map((row) => toNumber(row.lvr_apr_7d)) },
        ]
      : [
          { name: "Supply APY", values: rows.map((row) => toNumber(row.yield_apy)) },
          { name: "Borrow APY", values: rows.map((row) => toNumber(row.borrow_apy)) },
          { name: "Utilization", values: rows.map((row) => toNumber(row.utilization_rate)) },
        ];

  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { top: 0 },
    grid: { left: 48, right: 16, top: 44, bottom: 36 },
    xAxis: { type: "category", data: dates },
    yAxis: { type: "value" },
    series: primarySeries.map((series, index) => ({
      name: series.name,
      type: "line",
      smooth: true,
      showSymbol: false,
      lineStyle: { width: index === 0 ? 3 : 2 },
      areaStyle: index === 0 ? { opacity: isDark ? 0.15 : 0.08 } : undefined,
      data: series.values,
    })),
  };
}

function buildCompareOption(
  selected: Record<string, unknown>[],
  comparison: Record<string, unknown>[],
  primaryLabel: string,
  secondaryLabel: string,
) {
  if (!selected.length || !comparison.length) {
    return { title: { text: "Pick a comparison opportunity", left: "center", top: "center" } };
  }
  const selectedMap = new Map(selected.map((row) => [String(row.date ?? ""), toNumber(row.net_apr_7d ?? row.yield_apy)]));
  const comparisonMap = new Map(comparison.map((row) => [String(row.date ?? ""), toNumber(row.net_apr_7d ?? row.yield_apy)]));
  const dates = Array.from(new Set([...selectedMap.keys(), ...comparisonMap.keys()])).sort();
  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { top: 0, data: [primaryLabel, secondaryLabel] },
    grid: { left: 48, right: 16, top: 44, bottom: 36 },
    xAxis: { type: "category", data: dates },
    yAxis: { type: "value" },
    series: [
      {
        name: primaryLabel,
        type: "line",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 3 },
        data: dates.map((date) => selectedMap.get(date) ?? null),
      },
      {
        name: secondaryLabel,
        type: "line",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2 },
        data: dates.map((date) => comparisonMap.get(date) ?? null),
      },
    ],
  };
}

function buildSimulationOption(simulation?: SimulationResult | null) {
  if (!simulation || !simulation.series.length) {
    return { title: { text: "Run a simulation to see the curve", left: "center", top: "center" } };
  }
  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { top: 0, data: ["Portfolio value", "Gain"] },
    grid: { left: 48, right: 16, top: 44, bottom: 36 },
    xAxis: { type: "category", data: simulation.series.map((point) => point.date) },
    yAxis: { type: "value" },
    series: [
      {
        name: "Portfolio value",
        type: "line",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 3 },
        data: simulation.series.map((point) => point.value_usd),
      },
      {
        name: "Gain",
        type: "line",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2 },
        data: simulation.series.map((point) => point.gain_usd),
      },
    ],
  };
}

export default function YieldOpportunitiesApp() {
  const { view, callTool, fetchRows } = useMiniApp<YieldOpportunitiesState>({
    appId: APP_ID,
    mockPayload: MOCK_PAYLOAD,
  });
  const [query, setQuery] = useState("");
  const [compareDraft, setCompareDraft] = useState("");
  const [simulationMode, setSimulationMode] = useState<"forward" | "historical_replay">("forward");
  const [principal, setPrincipal] = useState("10000");
  const [simulationStart, setSimulationStart] = useState("");
  const [simulationEnd, setSimulationEnd] = useState("");
  const [compound, setCompound] = useState(true);
  // Stage-1 retrofit: split pending so filter refreshes don't freeze the
  // opportunity detail, and a running simulation doesn't block filters.
  const [loadingFilters, setLoadingFilters] = useState(false);
  const [loadingOpportunity, setLoadingOpportunity] = useState(false);
  const [loadingSimulation, setLoadingSimulation] = useState(false);
  const pending = loadingFilters || loadingOpportunity || loadingSimulation;
  const [activeTab, setActiveTab] = useState<YieldTab>("Overview");
  const isDark = document.documentElement.dataset.theme !== "light";

  const state = view?.view_state;
  const datasets = view?.datasets ?? {};
  const opportunities = useMemo(() => normalizeOpportunityRows(datasets.opportunities), [datasets.opportunities]);

  useEffect(() => {
    setQuery(state?.query ?? "");
  }, [state?.query]);

  useEffect(() => {
    setCompareDraft(state?.compare_with ?? "");
  }, [state?.compare_with]);

  useEffect(() => {
    setActiveTab(state?.active_tab ?? "Overview");
  }, [state?.selected_opportunity_key, state?.active_tab]);

  if (!view || !state) {
    return <div className="mini-app-loading">Loading Yield Opportunities…</div>;
  }

  const filtered = sortOpportunities(
    opportunities.filter((row) => {
      const tokenMatch = !state.filters.token || row.token === state.filters.token;
      const typeMatch = !state.filters.type || row.type.toLowerCase() === state.filters.type;
      const protocolMatch = !state.filters.protocol || row.protocol === state.filters.protocol;
      const haystack = `${row.token} ${row.name} ${row.protocol} ${row.type}`.toLowerCase();
      const queryMatch = !query.trim() || haystack.includes(query.trim().toLowerCase());
      return tokenMatch && typeMatch && protocolMatch && queryMatch;
    }),
    state.sort,
  );

  const selected = filtered.find((row) => row.opportunity_key === state.selected_opportunity_key)
    ?? opportunities.find((row) => row.opportunity_key === state.selected_opportunity_key)
    ?? null;
  const comparison = opportunities.find((row) => row.opportunity_key === state.compare_with) ?? null;
  const selectedHistory = historyRows(datasets.selected_history);
  const compareHistory = historyRows(datasets.compare_history);
  const historyOption = buildHistoryOption(selectedHistory, selected?.type ?? "LP", isDark);
  const compareOption = buildCompareOption(
    selectedHistory,
    compareHistory,
    selected?.name ?? "Selected",
    comparison?.name ?? "Comparison",
  );
  const simulationOption = buildSimulationOption(state.simulation ?? null);

  const tokenOptions = Array.from(new Set(opportunities.map((row) => row.token))).sort();
  const protocolOptions = Array.from(new Set(opportunities.map((row) => row.protocol))).sort();

  const patchFilters = async (patch: Partial<YieldFilters> & { sort?: SortKey }) => {
    setLoadingFilters(true);
    try {
      await callTool("update_yield_opportunities_focus", {
        view_id: view.view_id,
        sort: patch.sort ?? state.sort,
        token: patch.token ?? state.filters.token,
        type: patch.type ?? state.filters.type,
        protocol: patch.protocol ?? state.filters.protocol,
      });
    } finally {
      setLoadingFilters(false);
    }
  };

  const loadOpportunity = async (opportunityKey: string, compareWith = state.compare_with) => {
    setLoadingOpportunity(true);
    try {
      setActiveTab(compareWith ? "Compare" : "Overview");
      await callTool("load_yield_opportunity", {
        view_id: view.view_id,
        opportunity_key: opportunityKey,
        compare_with: compareWith,
      });
    } finally {
      setLoadingOpportunity(false);
    }
  };

  const runSimulation = async () => {
    if (!selected) return;
    setLoadingSimulation(true);
    try {
      setActiveTab("Simulation");
      await callTool("run_yield_simulation", {
        view_id: view.view_id,
        opportunity_key: selected.opportunity_key,
        mode: simulationMode,
        principal: Number(principal),
        start_date: simulationStart,
        end_date: simulationEnd,
        compound,
      });
    } finally {
      setLoadingSimulation(false);
    }
  };

  return (
    <div className="mini-app-root">
      <header className="mini-app-header">
        <div>
          <h1>{view.title}</h1>
          <div className="mini-app-subtitle">Rank, compare, inspect history, and run simple yield simulations.</div>
        </div>
        {pending ? <span className="mini-app-pill mini-app-pill--warning">Updating…</span> : null}
      </header>

      <WarningBanner warnings={view.warnings ?? []} />
      <SummaryCards cards={view.summary_cards ?? []} />

      <section className="mini-app-controls">
        <label className="mini-app-inline-field">
          <span>Search</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Token, protocol, name…" />
        </label>
        <label className="mini-app-inline-field">
          <span>Sort</span>
          <select value={state.sort} onChange={(event) => void patchFilters({ sort: event.target.value as SortKey })}>
            <option value="headline_rate_desc">Headline rate</option>
            <option value="headline_rate_asc">Headline rate (asc)</option>
            <option value="tvl_desc">TVL</option>
            <option value="fees_7d_desc">Fees 7D</option>
            <option value="volume_7d_desc">Volume 7D</option>
            <option value="utilization_desc">Utilization</option>
          </select>
        </label>
        <label className="mini-app-inline-field">
          <span>Token</span>
          <select value={state.filters.token} onChange={(event) => void patchFilters({ token: event.target.value })}>
            <option value="">All tokens</option>
            {tokenOptions.map((token) => <option key={token} value={token}>{token}</option>)}
          </select>
        </label>
        <label className="mini-app-inline-field">
          <span>Type</span>
          <SegmentedControl<string>
            ariaLabel="Filter by opportunity type"
            size="sm"
            value={state.filters.type || "all"}
            onChange={(v) => void patchFilters({ type: v === "all" ? "" : v })}
            options={[
              { value: "all", label: "All" },
              { value: "lp", label: "LP" },
              { value: "lending", label: "Lending" },
            ]}
          />
        </label>
        <label className="mini-app-inline-field">
          <span>Protocol</span>
          <select value={state.filters.protocol} onChange={(event) => void patchFilters({ protocol: event.target.value })}>
            <option value="">All protocols</option>
            {protocolOptions.map((protocol) => <option key={protocol} value={protocol}>{protocol}</option>)}
          </select>
        </label>
      </section>

      <section className="mini-app-split-layout">
        <div className="mini-app-panel mini-app-panel--narrow">
          <div className="mini-app-panel__header">
            <h2>Ranking</h2>
            <span>{filtered.length} rows</span>
          </div>
          <div className="mini-app-ranking-list">
            {filtered.map((row) => {
              const selectedRow = row.opportunity_key === state.selected_opportunity_key;
              return (
                <button
                  key={row.opportunity_key}
                  type="button"
                  className={`mini-app-ranking-item ${selectedRow ? "is-selected" : ""}`}
                  onClick={() => void loadOpportunity(row.opportunity_key)}
                >
                  <div className="mini-app-ranking-item__head">
                    <strong>{row.name}</strong>
                    <span>{formatNumber(row.headline_rate ?? 0)}%</span>
                  </div>
                  <div className="mini-app-ranking-item__meta">
                    <span>{row.protocol}</span>
                    <span>{row.token}</span>
                    <span>{row.type}</span>
                  </div>
                  <div className="mini-app-ranking-item__sub">
                    {row.type === "LP"
                      ? `${formatMoney(row.tvl ?? 0)} TVL`
                      : `${formatMoney(row.total_supplied ?? 0)} supplied`}
                  </div>
                </button>
              );
            })}
            {!filtered.length ? <div className="mini-app-unavailable">No opportunities match the current filters.</div> : null}
          </div>
        </div>

        <div className="mini-app-panel">
          <div className="mini-app-panel__header">
            <h2>{selected ? selected.name : "Opportunity detail"}</h2>
            <span>{selected ? selected.protocol : "Select a row"}</span>
          </div>

          {!selected ? (
            <div className="mini-app-unavailable">Select an opportunity from the ranking table to inspect history, compare, or simulate.</div>
          ) : (
            <div className="mini-app-detail-stack">
              <div className="mini-app-analysis__tabs">
                {(["Overview", "History", "Compare", "Simulation"] as YieldTab[]).map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    className={`mini-app-analysis__tab ${activeTab === tab ? "is-active" : ""}`}
                    onClick={() => setActiveTab(tab)}
                  >
                    {tab}
                  </button>
                ))}
              </div>

              <div className="mini-app-card-grid">
                <div className="mini-app-data-card">
                  <span>Headline rate</span>
                  <strong>{formatNumber(selected.headline_rate ?? 0)}%</strong>
                </div>
                <div className="mini-app-data-card">
                  <span>{selected.type === "LP" ? "TVL" : "Total supplied"}</span>
                  <strong>{formatMoney(selected.type === "LP" ? selected.tvl ?? 0 : selected.total_supplied ?? 0)}</strong>
                </div>
                <div className="mini-app-data-card">
                  <span>{selected.type === "LP" ? "Fees 7D" : "Borrow APY"}</span>
                  <strong>{selected.type === "LP" ? formatMoney(selected.fees_7d ?? 0) : `${formatNumber(selected.borrow_apy ?? 0)}%`}</strong>
                </div>
                <div className="mini-app-data-card">
                  <span>{selected.type === "LP" ? "LVR APR" : "Utilization"}</span>
                  <strong>{selected.type === "LP" ? `${formatNumber(selected.lvr_apr_7d ?? 0)}%` : `${formatNumber(selected.utilization_rate ?? 0)}%`}</strong>
                </div>
              </div>

              <section className="mini-app-analysis">
                {activeTab === "Overview" ? (
                  <div className="mini-app-overview-grid">
                    <div className="mini-app-data-card">
                      <span>Protocol</span>
                      <strong>{selected.protocol}</strong>
                    </div>
                    <div className="mini-app-data-card">
                      <span>Token</span>
                      <strong>{selected.token}</strong>
                    </div>
                    <div className="mini-app-data-card">
                      <span>{selected.type === "LP" ? "Fee tier" : "Borrow APY"}</span>
                      <strong>
                        {selected.type === "LP"
                          ? `${formatNumber(selected.fee_pct ?? 0, 3)}%`
                          : `${formatNumber(selected.borrow_apy ?? 0)}%`}
                      </strong>
                    </div>
                    <div className="mini-app-data-card">
                      <span>{selected.type === "LP" ? "Volume 7D" : "Total borrowed"}</span>
                      <strong>
                        {selected.type === "LP"
                          ? formatMoney(selected.volume_usd_7d ?? 0)
                          : formatMoney(selected.total_borrowed ?? 0)}
                      </strong>
                    </div>
                  </div>
                ) : null}

                {activeTab === "History" ? (
                  <>
                    <section className="mini-app-chart">
                      <ReactECharts option={historyOption} style={{ height: 320, width: "100%" }} theme={isDark ? "dark" : undefined} />
                    </section>
                    <DatasetTable
                      dataset={datasets.selected_history}
                      datasetKey="selected_history"
                      emptyLabel="No history is attached yet."
                      viewId={view.view_id}
                      fetchRows={fetchRows}
                    />
                  </>
                ) : null}

                {activeTab === "Compare" ? (
                  <>
                    <div className="mini-app-inline-toolbar">
                      <label className="mini-app-inline-field">
                        <span>Compare with</span>
                        <select
                          value={compareDraft}
                          onChange={(event) => {
                            const value = event.target.value;
                            setCompareDraft(value);
                            setActiveTab("Compare");
                            void loadOpportunity(selected.opportunity_key, value);
                          }}
                        >
                          <option value="">Choose one opportunity</option>
                          {opportunities
                            .filter((row) => row.opportunity_key !== selected.opportunity_key)
                            .map((row) => (
                              <option key={row.opportunity_key} value={row.opportunity_key}>
                                {row.name} · {row.protocol}
                              </option>
                            ))}
                        </select>
                      </label>
                    </div>
                    <section className="mini-app-chart">
                      <ReactECharts option={compareOption} style={{ height: 320, width: "100%" }} theme={isDark ? "dark" : undefined} />
                    </section>
                    <div className="mini-app-comparison-grid">
                      <div className="mini-app-data-card">
                        <span>{selected.name}</span>
                        <strong>{formatNumber(selected.headline_rate ?? 0)}%</strong>
                        <small>{selected.type === "LP" ? formatMoney(selected.tvl ?? 0) : formatMoney(selected.total_supplied ?? 0)}</small>
                      </div>
                      <div className="mini-app-data-card">
                        <span>{comparison?.name ?? "Comparison"}</span>
                        <strong>{comparison ? `${formatNumber(comparison.headline_rate ?? 0)}%` : "—"}</strong>
                        <small>
                          {comparison
                            ? comparison.type === "LP"
                              ? formatMoney(comparison.tvl ?? 0)
                              : formatMoney(comparison.total_supplied ?? 0)
                            : "Pick one opportunity"}
                        </small>
                      </div>
                    </div>
                  </>
                ) : null}

                {activeTab === "Simulation" ? (
                  <>
                    <div className="mini-app-form-grid">
                      <label className="mini-app-inline-field">
                        <span>Mode</span>
                        <select value={simulationMode} onChange={(event) => setSimulationMode(event.target.value as "forward" | "historical_replay")}>
                          <option value="forward">Forward</option>
                          <option value="historical_replay">Historical replay</option>
                        </select>
                      </label>
                      <label className="mini-app-inline-field">
                        <span>Principal (USD)</span>
                        <input value={principal} onChange={(event) => setPrincipal(event.target.value)} />
                      </label>
                      <label className="mini-app-inline-field">
                        <span>Start date</span>
                        <input type="date" value={simulationStart} onChange={(event) => setSimulationStart(event.target.value)} />
                      </label>
                      <label className="mini-app-inline-field">
                        <span>End date</span>
                        <input type="date" value={simulationEnd} onChange={(event) => setSimulationEnd(event.target.value)} />
                      </label>
                      <label className="mini-app-inline-checkbox">
                        <input type="checkbox" checked={compound} onChange={(event) => setCompound(event.target.checked)} />
                        <span>Compound returns</span>
                      </label>
                      <AsyncButton variant="primary" loadingLabel="Simulating" onClick={runSimulation}>
                        Run simulation
                      </AsyncButton>
                    </div>
                    <div className="mini-app-card-grid">
                      <div className="mini-app-data-card">
                        <span>Ending value</span>
                        <strong>{state.simulation ? formatMoney(state.simulation.ending_value_usd) : "—"}</strong>
                      </div>
                      <div className="mini-app-data-card">
                        <span>Gain</span>
                        <strong>{state.simulation ? formatMoney(state.simulation.gain_usd) : "—"}</strong>
                      </div>
                      <div className="mini-app-data-card">
                        <span>Return</span>
                        <strong>{state.simulation ? `${formatNumber(state.simulation.return_pct, 2)}%` : "—"}</strong>
                      </div>
                      <div className="mini-app-data-card">
                        <span>Annualized</span>
                        <strong>{state.simulation ? `${formatNumber(state.simulation.annualized_return_pct, 2)}%` : "—"}</strong>
                      </div>
                    </div>
                    <section className="mini-app-chart">
                      <ReactECharts option={simulationOption} style={{ height: 320, width: "100%" }} theme={isDark ? "dark" : undefined} />
                    </section>
                  </>
                ) : null}
              </section>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
