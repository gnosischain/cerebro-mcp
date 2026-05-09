import { useEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import { useMiniApp } from "../shared/useMiniApp";
import { WarningBanner } from "../shared/WarningBanner";
import { CollapsibleSection } from "../shared/CollapsibleSection";
import { MiniAppChrome } from "../shared/MiniAppChrome";
import type {
  DatasetDescriptor,
  MiniAppPayload,
} from "../shared/miniAppTypes";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TokenCatalogEntry {
  key: string;
  symbol: string;
  name: string;
  address: string;
  decimals: number;
  has_price: boolean;
}

type TokenMetric =
  | "bridge_volume"
  | "bridge_txs"
  | "lp_count"
  | "price"
  | "holders"
  | "pool_tvl"
  | "pool_volume"
  | "growth";
type TokenDirection = "inbound" | "outbound" | "both" | "";

interface TokenExplorerState {
  mode: "empty" | "loaded" | "comparison";
  token_catalog: TokenCatalogEntry[];
  selected_token: string;
  selected_tokens?: string[];
  comparison_mode?: boolean;
  start_date: string;
  include_price: boolean;
  selected_metric: TokenMetric;
  bridge: string;
  direction: TokenDirection;
  growth_source?: string;
  growth_window?: string;
  supply_unavailable?: boolean;
}

const APP_ID = "token_explorer";

// ---------------------------------------------------------------------------
// Mock payload for dev mode
// ---------------------------------------------------------------------------

const MOCK_CATALOG: TokenCatalogEntry[] = [
  { key: "gno", symbol: "GNO", name: "Gnosis", address: "0x9c58...", decimals: 18, has_price: true },
  { key: "wxdai", symbol: "WXDAI", name: "Wrapped xDAI", address: "0xe91d...", decimals: 18, has_price: true },
  { key: "usdc", symbol: "USDC", name: "USD Coin", address: "0xddaf...", decimals: 6, has_price: true },
];

const MOCK_LOADED =
  typeof window !== "undefined" &&
  new URLSearchParams(window.location.search).get("demo") === "loaded";

const MOCK_DATASET: DatasetDescriptor = {
  key: "primary",
  title: "gno bridge volume",
  sql: "SELECT * FROM dbt.api_bridges_volume_daily WHERE symbol='GNO'",
  database: "dbt",
  columns: [
    { name: "day", type: "Date" },
    { name: "volume_usd", type: "number" },
    { name: "txs", type: "number" },
  ],
  stats: { row_count: 5, rows_returned: 5, mode: "exact_bounded", warnings: [] },
  preview_rows: [
    ["2026-03-05", 1_420_000, 312],
    ["2026-03-06", 1_610_000, 341],
    ["2026-03-07", 1_280_000, 299],
    ["2026-03-08", 1_880_000, 402],
    ["2026-03-09", 1_520_000, 355],
  ],
};

const MOCK_PAYLOAD: MiniAppPayload<TokenExplorerState> = {
  type: "INITIAL_LOAD",
  view_id: "dev-view",
  app_id: APP_ID,
  title: "Token Explorer",
  status: "ready",
  summary_cards: MOCK_LOADED
    ? [
        { label: "Token", value: "GNO", tone: "neutral" },
        { label: "Metric", value: "bridge_volume", tone: "neutral" },
        { label: "Rows", value: "5", tone: "positive" },
      ]
    : [
        { label: "Tokens available", value: String(MOCK_CATALOG.length), tone: "neutral" },
      ],
  datasets: MOCK_LOADED ? { primary: MOCK_DATASET } : {},
  view_state: {
    mode: MOCK_LOADED ? "loaded" : "empty",
    token_catalog: MOCK_CATALOG,
    selected_token: MOCK_LOADED ? "GNO" : "",
    start_date: "2024-01-01",
    include_price: true,
    selected_metric: "bridge_volume",
    bridge: "",
    direction: "both",
  },
  warnings: [],
};

// ---------------------------------------------------------------------------
// Chart helpers
// ---------------------------------------------------------------------------

function metricColumn(metric: TokenMetric): string {
  switch (metric) {
    case "bridge_volume": return "volume_usd";
    case "bridge_txs": return "txs";
    case "lp_count": return "unique_lp_count";
    case "price": return "price_usd";
    case "holders": return "holder_count";
    case "pool_tvl": return "tvl_usd";
    case "pool_volume": return "volume_usd";
    case "growth": return "volume_usd";
  }
}

function datasetForMetric(
  metric: TokenMetric,
  datasets: Record<string, DatasetDescriptor> | undefined,
  prefix = "",
): DatasetDescriptor | undefined {
  if (!datasets) return undefined;
  const p = prefix;
  switch (metric) {
    case "bridge_volume":
    case "bridge_txs":
      return datasets[`${p}bridge_flows`];
    case "lp_count":
      return datasets[`${p}lp_counts`];
    case "price":
      return datasets[`${p}price_history`];
    case "holders":
      return datasets[`${p}holders`];
    case "pool_tvl":
      return datasets[`${p}pool_tvl`];
    case "pool_volume":
      return datasets[`${p}pool_volume`];
    case "growth":
      return datasets[`${p}bridge_flows`];
  }
}

/** All metric tabs — always shown, greyed out if no data */
const ALL_METRICS: { key: TokenMetric; label: string; dsKey: string }[] = [
  { key: "bridge_volume", label: "Bridge vol.", dsKey: "bridge_flows" },
  { key: "bridge_txs", label: "Bridge txs", dsKey: "bridge_flows" },
  { key: "holders", label: "Holders", dsKey: "holders" },
  { key: "pool_tvl", label: "Pool TVL", dsKey: "pool_tvl" },
  { key: "pool_volume", label: "Pool vol.", dsKey: "pool_volume" },
  { key: "price", label: "Price", dsKey: "price_history" },
  { key: "lp_count", label: "LPs", dsKey: "lp_counts" },
  { key: "growth", label: "Growth", dsKey: "bridge_flows" },
];

function sortByDate(xs: string[], ys: number[]): void {
  if (xs.length === 0 || !/^\d{4}-\d{2}/.test(xs[0])) return;
  const paired = xs.map((x, i) => ({ x, y: ys[i] }));
  paired.sort((a, b) => a.x.localeCompare(b.x));
  for (let i = 0; i < paired.length; i++) {
    xs[i] = paired[i].x;
    ys[i] = paired[i].y;
  }
}

function buildEChartsOption(
  dataset: DatasetDescriptor | undefined,
  metricColumnName: string,
  bridgeFilter: string,
  direction: TokenDirection,
  isDark: boolean,
  secondaryDataset?: DatasetDescriptor,
  secondaryLabel?: string,
  primaryLabel?: string,
): Record<string, unknown> {
  if (!dataset || dataset.preview_rows.length === 0) {
    return { title: { text: "No data", left: "center", top: "center" } };
  }
  const dateIdx = dataset.columns.findIndex((c) => c.name === "date" || c.name === "window");
  const valueIdx = dataset.columns.findIndex((c) => c.name === metricColumnName);
  const bridgeIdx = dataset.columns.findIndex((c) => c.name === "bridge");
  const dirIdx = dataset.columns.findIndex((c) => c.name === "direction");
  if (valueIdx < 0) {
    return { title: { text: `Column '${metricColumnName}' not in dataset`, left: "center", top: "center" } };
  }

  let filteredRows = dataset.preview_rows;
  if (bridgeFilter && bridgeIdx >= 0) filteredRows = filteredRows.filter((r) => r[bridgeIdx] === bridgeFilter);
  if (direction && direction !== "both" && dirIdx >= 0) filteredRows = filteredRows.filter((r) => r[dirIdx] === direction);

  const xs = filteredRows.map((r) => String(r[dateIdx >= 0 ? dateIdx : 0] ?? ""));
  const ys = filteredRows.map((r) => Number(r[valueIdx] ?? 0));
  sortByDate(xs, ys);

  const series: unknown[] = [
    {
      name: primaryLabel || metricColumnName,
      type: "line",
      smooth: true,
      showSymbol: false,
      data: ys,
      lineStyle: { width: 2 },
      areaStyle: { opacity: isDark ? 0.18 : 0.12 },
    },
  ];

  const yAxes: unknown[] = [{ type: "value" }];

  // Comparison overlay
  if (secondaryDataset && secondaryDataset.preview_rows.length > 0) {
    const sDateIdx = secondaryDataset.columns.findIndex((c) => c.name === "date" || c.name === "window");
    const sValIdx = secondaryDataset.columns.findIndex((c) => c.name === metricColumnName);
    if (sValIdx >= 0) {
      let sRows = secondaryDataset.preview_rows;
      const sBridgeIdx = secondaryDataset.columns.findIndex((c) => c.name === "bridge");
      const sDirIdx = secondaryDataset.columns.findIndex((c) => c.name === "direction");
      if (bridgeFilter && sBridgeIdx >= 0) sRows = sRows.filter((r) => r[sBridgeIdx] === bridgeFilter);
      if (direction && direction !== "both" && sDirIdx >= 0) sRows = sRows.filter((r) => r[sDirIdx] === direction);

      const sxs = sRows.map((r) => String(r[sDateIdx >= 0 ? sDateIdx : 0] ?? ""));
      const sys = sRows.map((r) => Number(r[sValIdx] ?? 0));
      sortByDate(sxs, sys);

      // Merge dates: use the union of both date sets
      const allDates = Array.from(new Set([...xs, ...sxs])).sort();
      const primaryMap = new Map(xs.map((x, i) => [x, ys[i]]));
      const secondaryMap = new Map(sxs.map((x, i) => [x, sys[i]]));

      series[0] = {
        ...(series[0] as Record<string, unknown>),
        data: allDates.map((d) => primaryMap.get(d) ?? null),
      };

      yAxes.push({ type: "value", position: "right" });
      series.push({
        name: secondaryLabel || "comparison",
        type: "line",
        smooth: true,
        showSymbol: false,
        yAxisIndex: 1,
        data: allDates.map((d) => secondaryMap.get(d) ?? null),
        lineStyle: { width: 2 },
        areaStyle: { opacity: isDark ? 0.1 : 0.06 },
      });

      return {
        backgroundColor: "transparent",
        tooltip: { trigger: "axis" },
        legend: { data: [primaryLabel || metricColumnName, secondaryLabel || "comparison"] },
        grid: { left: 60, right: 60, top: 48, bottom: 48 },
        xAxis: { type: "category", data: allDates },
        yAxis: yAxes,
        series,
      };
    }
  }

  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    grid: { left: 60, right: 24, top: 36, bottom: 48 },
    xAxis: { type: "category", data: xs },
    yAxis: { type: "value" },
    series,
  };
}

// ---------------------------------------------------------------------------
// Growth calculation (frontend-derived)
// ---------------------------------------------------------------------------

type GrowthWindow = "wow" | "mom";

function computeGrowth(
  dataset: DatasetDescriptor | undefined,
  valueCol: string,
  window: GrowthWindow,
): { dates: string[]; values: number[] } {
  if (!dataset || dataset.preview_rows.length === 0) return { dates: [], values: [] };
  const dateIdx = dataset.columns.findIndex((c) => c.name === "date" || c.name === "window");
  const valIdx = dataset.columns.findIndex((c) => c.name === valueCol);
  if (valIdx < 0) return { dates: [], values: [] };

  // Aggregate by period
  const buckets = new Map<string, number>();
  for (const row of dataset.preview_rows) {
    const d = String(row[dateIdx >= 0 ? dateIdx : 0] ?? "");
    const v = Number(row[valIdx] ?? 0);
    const key = window === "mom" ? d.slice(0, 7) : d; // month or day
    buckets.set(key, (buckets.get(key) ?? 0) + v);
  }

  const sorted = Array.from(buckets.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  const dates: string[] = [];
  const values: number[] = [];
  for (let i = 1; i < sorted.length; i++) {
    const prev = sorted[i - 1][1];
    const curr = sorted[i][1];
    if (prev === 0) continue;
    dates.push(sorted[i][0]);
    values.push(((curr - prev) / prev) * 100);
  }
  return { dates, values };
}

// ---------------------------------------------------------------------------
// Analysis helpers
// ---------------------------------------------------------------------------

function isNumericColumn(rows: unknown[][], idx: number): boolean {
  let seen = 0;
  for (const row of rows) {
    const v = row[idx];
    if (v === null || v === undefined || v === "") continue;
    if (typeof v === "number") seen++;
    else if (typeof v === "string" && !isNaN(Number(v))) seen++;
    else return false;
    if (seen >= 10) return true;
  }
  return seen > 0;
}

function computeStats(rows: unknown[][], columns: string[]) {
  const out: { name: string; count: number; min: number; max: number; mean: number; median: number }[] = [];
  for (let idx = 0; idx < columns.length; idx++) {
    if (!isNumericColumn(rows, idx)) continue;
    const vals: number[] = [];
    for (const row of rows) {
      const n = Number(row[idx]);
      if (Number.isFinite(n)) vals.push(n);
    }
    if (vals.length === 0) continue;
    const sorted = [...vals].sort((a, b) => a - b);
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    const mid = Math.floor(sorted.length / 2);
    out.push({
      name: columns[idx],
      count: vals.length,
      min: sorted[0],
      max: sorted[sorted.length - 1],
      mean,
      median: sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid],
    });
  }
  return out;
}

function fmtNum(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(2) + "k";
  return n.toFixed(2);
}

function TokenAnalysisPanel({ rows, columns }: { rows: unknown[][]; columns: string[] }) {
  const stats = useMemo(() => computeStats(rows, columns), [rows, columns]);
  if (stats.length === 0) return null;
  return (
    <div className="mini-app-analysis">
      <div className="mini-app-analysis__tabs">
        <button type="button" className="mini-app-analysis__tab is-active">
          Summary ({stats.length})
        </button>
      </div>
      <div className="mini-app-table-wrap">
        <table>
          <thead><tr><th>Column</th><th>Count</th><th>Min</th><th>Mean</th><th>Median</th><th>Max</th></tr></thead>
          <tbody>
            {stats.map((s) => (
              <tr key={s.name}>
                <td>{s.name}</td><td>{s.count.toLocaleString()}</td>
                <td>{fmtNum(s.min)}</td><td>{fmtNum(s.mean)}</td>
                <td>{fmtNum(s.median)}</td><td>{fmtNum(s.max)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chart info panel
// ---------------------------------------------------------------------------

function ChartInfoPanel({ metric, token, compareToken, rows, datasetMode }: {
  metric: string; token: string; compareToken?: string; rows: number; datasetMode?: string;
}) {
  return (
    <CollapsibleSection title="Chart context" defaultOpen tone="subtle">
      <div className="mini-app-chart-info__content">
        <span className="mini-app-chart-info__item">
          <strong>Token:</strong> {token}
          {compareToken ? ` vs ${compareToken}` : ""}
        </span>
        <span className="mini-app-chart-info__item"><strong>Metric:</strong> {metric}</span>
        <span className="mini-app-chart-info__item"><strong>Rows:</strong> {rows.toLocaleString()}</span>
        {datasetMode && (
          <span className="mini-app-chart-info__item">
            <strong>Mode:</strong> {datasetMode}
          </span>
        )}
      </div>
    </CollapsibleSection>
  );
}

// ---------------------------------------------------------------------------
// Token picker (always visible, supports 1 or 2 tokens)
// ---------------------------------------------------------------------------

interface TokenPickerProps {
  catalog: TokenCatalogEntry[];
  initialStartDate: string;
  initialIncludePrice: boolean;
  loading: boolean;
  errorMessage: string | null;
  compact?: boolean;
  onLoad: (config: {
    symbol: string;
    compareWith?: string;
    startDate: string;
    includePrice: boolean;
  }) => void;
}

function TokenPicker({
  catalog, initialStartDate, initialIncludePrice, loading, errorMessage, compact, onLoad,
}: TokenPickerProps) {
  const [search, setSearch] = useState("");
  const [primary, setPrimary] = useState<TokenCatalogEntry | null>(null);
  const [compare, setCompare] = useState<TokenCatalogEntry | null>(null);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [startDate, setStartDate] = useState(initialStartDate);
  const [includePrice, setIncludePrice] = useState(initialIncludePrice);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setDropdownOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const matches = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return catalog.slice(0, 12);
    return catalog.filter((t) => `${t.symbol} ${t.name} ${t.address}`.toLowerCase().includes(q)).slice(0, 12);
  }, [catalog, search]);

  const pick = (t: TokenCatalogEntry) => {
    if (!primary) {
      setPrimary(t);
    } else if (!compare && t.key !== primary.key) {
      setCompare(t);
    }
    setSearch("");
    setDropdownOpen(false);
  };

  const handleLoad = () => {
    if (!primary) return;
    onLoad({
      symbol: primary.symbol,
      compareWith: compare?.symbol,
      startDate,
      includePrice,
    });
  };

  return (
    <div className={`mini-app-picker mini-app-picker--compact${compact ? " mini-app-picker--shrink" : ""}`}>
      <div className="mini-app-picker__head">
        <h2 className="mini-app-picker__title">Pick a token{compare ? " (comparison)" : ""}</h2>
        <span className="mini-app-picker__hint">{catalog.length} tokens</span>
      </div>

      {errorMessage && <div className="mini-app-picker__error">{errorMessage}</div>}

      <div className="mini-app-picker__compact-row">
        <div className="mini-app-picker__combo" ref={wrapRef}>
          <input
            type="search"
            className="mini-app-picker__combo-input"
            placeholder={
              !primary ? "Search token symbol, name, or address..." :
              !compare ? "Add a second token to compare (optional)..." : "Two tokens selected"
            }
            value={search}
            onChange={(e) => { setSearch(e.target.value); setDropdownOpen(true); }}
            onFocus={() => setDropdownOpen(true)}
            disabled={!!primary && !!compare}
          />
          <span className="mini-app-picker__count-pill">{catalog.length}</span>
          {dropdownOpen && matches.length > 0 && (
            <div className="mini-app-picker__dropdown">
              {matches.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  className="mini-app-picker__dropdown-item"
                  disabled={(primary?.key === t.key) || (compare?.key === t.key)}
                  onMouseDown={(e) => { e.preventDefault(); pick(t); }}
                >
                  <strong>{t.symbol}</strong> — {t.name}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="mini-app-picker__basket">
        {primary ? (
          <>
            <div className="mini-app-picker__chip">
              <span className="mini-app-picker__kind-pill">1</span>
              <strong>{primary.symbol}</strong> {primary.name}
              <button type="button" className="mini-app-picker__chip-remove" onClick={() => { setPrimary(compare); setCompare(null); }}>×</button>
            </div>
            {compare && (
              <div className="mini-app-picker__chip">
                <span className="mini-app-picker__kind-pill">2</span>
                <strong>{compare.symbol}</strong> {compare.name}
                <button type="button" className="mini-app-picker__chip-remove" onClick={() => setCompare(null)}>×</button>
              </div>
            )}
          </>
        ) : (
          <div className="mini-app-picker__basket-empty">No token selected — pick one above.</div>
        )}
      </div>

      {/* Inline options */}
      <div className="mini-app-picker__fieldset--row">
        <label>
          Start date
          <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label className="mini-app-picker__checkbox">
          <input
            type="checkbox"
            checked={includePrice}
            onChange={(e) => setIncludePrice(e.target.checked)}
            disabled={!!primary && !primary.has_price}
          />
          Price history
        </label>
      </div>

      <div className="mini-app-picker__actions">
        <button
          type="button"
          className="mini-app-picker__load-btn"
          onClick={handleLoad}
          disabled={loading || !primary}
        >
          {loading ? "Loading..." : compare ? `Compare ${primary?.symbol} vs ${compare.symbol}` : "Load token"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Loaded view
// ---------------------------------------------------------------------------

interface LoadedViewProps {
  view: MiniAppPayload<TokenExplorerState>;
  isDark: boolean;
  onUpdateFocus: (metric: TokenMetric, bridge: string, direction: TokenDirection) => void;
}

function LoadedView({ view, isDark, onUpdateFocus }: LoadedViewProps) {
  const state = view.view_state!;
  const selectedMetric = state.selected_metric ?? "bridge_volume";
  const bridgeFilter = state.bridge ?? "";
  const direction = state.direction ?? "both";
  const isComparison = state.comparison_mode === true;
  const tokens = state.selected_tokens ?? [state.selected_token];
  const [growthSource, setGrowthSource] = useState<string>(state.growth_source ?? "bridge_volume");
  const [growthWindow, setGrowthWindow] = useState<GrowthWindow>((state.growth_window ?? "wow") as GrowthWindow);

  // Tab data availability
  const tabAvailability = useMemo(() => {
    const map = new Map<string, boolean>();
    for (const m of ALL_METRICS) {
      if (m.key === "growth") {
        // Growth is available if any time-series dataset exists
        map.set(m.key, !!(view.datasets?.bridge_flows?.preview_rows?.length || view.datasets?.price_history?.preview_rows?.length));
      } else {
        const ds = view.datasets?.[m.dsKey];
        map.set(m.key, !!(ds && ds.preview_rows?.length > 0));
      }
    }
    return map;
  }, [view.datasets]);

  // Fall back to first available tab if current is unavailable.
  // Uses ref for onUpdateFocus to avoid dependency cycle.
  const onUpdateFocusRef = useRef(onUpdateFocus);
  onUpdateFocusRef.current = onUpdateFocus;
  useEffect(() => {
    // Only run fallback when there's actual data loaded
    const hasAnyData = Array.from(tabAvailability.values()).some(Boolean);
    if (!hasAnyData) return;
    if (!tabAvailability.get(selectedMetric)) {
      const first = ALL_METRICS.find((m) => tabAvailability.get(m.key));
      if (first) onUpdateFocusRef.current(first.key, bridgeFilter, direction);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabAvailability, selectedMetric]);

  const activeDS = datasetForMetric(selectedMetric, view.datasets);
  const secondaryDS = isComparison ? datasetForMetric(selectedMetric, view.datasets, "secondary_") : undefined;

  const isBridgeMetric = selectedMetric === "bridge_volume" || selectedMetric === "bridge_txs";
  const isGrowthTab = selectedMetric === "growth";

  // Bridge filter options
  const bridgeOptions = useMemo(() => {
    const ds = view.datasets?.bridge_flows;
    if (!ds) return [] as string[];
    const idx = ds.columns.findIndex((c) => c.name === "bridge");
    if (idx < 0) return [] as string[];
    const set = new Set<string>();
    for (const row of ds.preview_rows) {
      const v = row[idx];
      if (typeof v === "string" && v) set.add(v);
    }
    return Array.from(set).sort();
  }, [view.datasets]);

  // Growth chart data
  const growthData = useMemo(() => {
    if (!isGrowthTab) return null;
    const srcMetric = growthSource as TokenMetric;
    const ds = datasetForMetric(srcMetric, view.datasets);
    const col = metricColumn(srcMetric);
    return computeGrowth(ds, col, growthWindow);
  }, [isGrowthTab, growthSource, growthWindow, view.datasets]);

  const growthOption = useMemo(() => {
    if (!growthData || growthData.dates.length === 0) return { title: { text: "No growth data", left: "center", top: "center" } };
    return {
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", valueFormatter: (v: number) => `${v.toFixed(1)}%` },
      grid: { left: 60, right: 24, top: 36, bottom: 48 },
      xAxis: { type: "category", data: growthData.dates },
      yAxis: { type: "value", axisLabel: { formatter: "{value}%" } },
      series: [{
        type: "bar",
        data: growthData.values,
        itemStyle: {
          color: (p: { value: number }) => p.value >= 0 ? (isDark ? "#34d399" : "#10b981") : (isDark ? "#f87171" : "#ef4444"),
        },
      }],
    };
  }, [growthData, isDark]);

  // Main chart
  const chartOption = useMemo(() => {
    if (isGrowthTab) return growthOption;
    return buildEChartsOption(
      activeDS, metricColumn(selectedMetric), bridgeFilter, direction, isDark,
      secondaryDS, isComparison ? tokens[1] : undefined, isComparison ? tokens[0] : undefined,
    );
  }, [activeDS, secondaryDS, selectedMetric, bridgeFilter, direction, isDark, isGrowthTab, growthOption, isComparison, tokens]);

  // Available growth sources
  const growthSources = useMemo(() => {
    return ALL_METRICS
      .filter((m) => m.key !== "growth" && m.key !== "lp_count" && tabAvailability.get(m.key))
      .map((m) => ({ key: m.key, label: m.label }));
  }, [tabAvailability]);

  return (
    <>
      {/* Summary cards */}
      <section className="mini-app-summary-grid">
        {(view.summary_cards ?? []).map((card, i) => (
          <div key={i} className={`mini-app-summary-card tone-${card.tone ?? "neutral"}`}>
            <div className="mini-app-summary-label">{card.label}</div>
            <div className="mini-app-summary-value">{card.value}</div>
            {card.delta && <div className="mini-app-summary-delta">{card.delta}</div>}
          </div>
        ))}
      </section>

      {/* Tab bar — all tabs visible, greyed out if no data */}
      <div className="mini-app-analysis__tabs">
        {ALL_METRICS.map((m) => {
          const hasData = tabAvailability.get(m.key) ?? false;
          return (
            <button
              key={m.key}
              type="button"
              className={`mini-app-analysis__tab ${selectedMetric === m.key ? "is-active" : ""}`}
              disabled={!hasData}
              onClick={() => hasData && onUpdateFocus(m.key, bridgeFilter, direction)}
            >
              {m.label}
            </button>
          );
        })}
      </div>

      {/* Growth tab controls */}
      {isGrowthTab && (
        <div className="mini-app-growth-controls">
          <span>Source:</span>
          <select value={growthSource} onChange={(e) => setGrowthSource(e.target.value)}>
            {growthSources.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <span>Window:</span>
          <select value={growthWindow} onChange={(e) => setGrowthWindow(e.target.value as GrowthWindow)}>
            <option value="wow">Week-over-week</option>
            <option value="mom">Month-over-month</option>
          </select>
        </div>
      )}

      {/* Bridge-specific filters */}
      {isBridgeMetric && (
        <section className="mini-app-controls">
          <fieldset>
            <legend>Bridge</legend>
            <select value={bridgeFilter} onChange={(e) => onUpdateFocus(selectedMetric, e.target.value, direction)}>
              <option value="">All bridges</option>
              {bridgeOptions.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </fieldset>
          <fieldset>
            <legend>Direction</legend>
            {(["both", "inbound", "outbound"] as const).map((d) => (
              <label key={d}>
                <input type="radio" name="te-direction" value={d} checked={direction === d} onChange={() => onUpdateFocus(selectedMetric, bridgeFilter, d)} />
                {d}
              </label>
            ))}
          </fieldset>
        </section>
      )}

      {/* Chart info panel */}
      <ChartInfoPanel
        metric={selectedMetric}
        token={tokens[0]}
        compareToken={isComparison ? tokens[1] : undefined}
        rows={activeDS?.preview_rows.length ?? 0}
        datasetMode={activeDS?.stats?.mode}
      />

      {/* Main chart */}
      <section className="mini-app-chart">
        <ReactECharts
          option={chartOption}
          notMerge={true}
          style={{ height: 360, width: "100%" }}
          theme={isDark ? "dark" : undefined}
        />
      </section>

      {/* Comparison scatter (for time-series metrics, not lp_count or growth) */}
      {isComparison && !isGrowthTab && selectedMetric !== "lp_count" && activeDS && secondaryDS && (
        <section className="mini-app-chart">
          <div style={{ fontSize: "0.82rem", color: "var(--text-muted)", padding: "0 0 8px" }}>
            Correlation: {tokens[0]} vs {tokens[1]} ({selectedMetric})
          </div>
          <ReactECharts
            option={buildCorrelationScatter(activeDS, secondaryDS, metricColumn(selectedMetric), tokens[0], tokens[1], isDark)}
            notMerge={true}
            style={{ height: 300, width: "100%" }}
            theme={isDark ? "dark" : undefined}
          />
        </section>
      )}

      {isComparison && selectedMetric === "lp_count" && (
        <div className="mini-app-unavailable">
          Correlation scatter not available for LP count — use Bridge vol. or Price for correlation analysis.
        </div>
      )}

      {/* Analysis panel */}
      {activeDS && activeDS.preview_rows.length > 0 && !isGrowthTab && (
        <TokenAnalysisPanel rows={activeDS.preview_rows} columns={activeDS.columns.map((c) => c.name)} />
      )}
    </>
  );
}

// Scatter plot for correlation between two tokens
function buildCorrelationScatter(
  ds1: DatasetDescriptor, ds2: DatasetDescriptor, col: string,
  label1: string, label2: string, isDark: boolean,
): Record<string, unknown> {
  const dateIdx1 = ds1.columns.findIndex((c) => c.name === "date" || c.name === "window");
  const valIdx1 = ds1.columns.findIndex((c) => c.name === col);
  const dateIdx2 = ds2.columns.findIndex((c) => c.name === "date" || c.name === "window");
  const valIdx2 = ds2.columns.findIndex((c) => c.name === col);
  if (valIdx1 < 0 || valIdx2 < 0) return { title: { text: "Cannot build scatter", left: "center", top: "center" } };

  const map1 = new Map<string, number>();
  for (const r of ds1.preview_rows) map1.set(String(r[dateIdx1] ?? ""), Number(r[valIdx1] ?? 0));
  const data: [number, number][] = [];
  for (const r of ds2.preview_rows) {
    const d = String(r[dateIdx2] ?? "");
    if (map1.has(d)) data.push([map1.get(d)!, Number(r[valIdx2] ?? 0)]);
  }

  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "item" },
    grid: { left: 60, right: 24, top: 24, bottom: 48 },
    xAxis: { type: "value", name: label1 },
    yAxis: { type: "value", name: label2 },
    series: [{ type: "scatter", data, symbolSize: 6, itemStyle: { opacity: 0.7, color: isDark ? "#67e8f9" : "#0891b2" } }],
  };
}

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

export default function TokenExplorerApp() {
  const { view, callTool, updateModelContext } =
    useMiniApp<TokenExplorerState>({
      appId: APP_ID,
      mockPayload: MOCK_PAYLOAD,
    });

  const [isDark, setIsDark] = useState(() => document.documentElement.dataset.theme !== "light");
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    const obs = new MutationObserver(() => setIsDark(document.documentElement.dataset.theme !== "light"));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    setLoading(false);
    setErrorMessage(null);
  }, [view?.view_id, view?.view_state?.selected_token]);

  useEffect(() => {
    if (!view) return;
    const s = view.view_state;
    updateModelContext({
      view_id: view.view_id,
      mode: s?.mode ?? "empty",
      token: s?.selected_token || "n/a",
      comparison: s?.comparison_mode ? s?.selected_tokens?.join(",") : "none",
      selected_metric: s?.selected_metric ?? "n/a",
      bridge: s?.bridge ?? "",
      direction: s?.direction ?? "",
    });
  }, [view, updateModelContext]);

  const handleLoadToken = async (config: {
    symbol: string;
    compareWith?: string;
    startDate: string;
    includePrice: boolean;
  }) => {
    if (!view) return;
    setLoading(true);
    setErrorMessage(null);
    try {
      const args: Record<string, unknown> = {
        view_id: view.view_id,
        symbol_or_address: config.symbol,
        start_date: config.startDate,
        include_price: config.includePrice,
      };
      if (config.compareWith) args.compare_with = config.compareWith;
      await callTool("load_token_explorer_token", args);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateFocus = async (metric: TokenMetric, bridge: string, direction: TokenDirection) => {
    if (!view) return;
    try {
      await callTool("update_token_explorer_focus", { view_id: view.view_id, metric, bridge, direction });
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : String(err));
    }
  };

  if (!view) return <MiniAppChrome activeTabId="token"><div className="mini-app-loading">Loading Token Explorer...</div></MiniAppChrome>;

  const state = view.view_state;
  const hasData = !!((state?.mode === "loaded" || state?.mode === "comparison") && view.datasets && Object.keys(view.datasets).length > 0);

  return (
    <MiniAppChrome activeTabId="token">
    <div className="mini-app-root mini-app-token-explorer">
      <header className="mini-app-header">
        <h1>{view.title}</h1>
        <span className="mini-app-subtitle">view_id: {view.view_id.slice(0, 8)}</span>
      </header>

      <WarningBanner warnings={view.warnings ?? []} />

      <TokenPicker
        catalog={state?.token_catalog ?? []}
        initialStartDate={state?.start_date ?? "2024-01-01"}
        initialIncludePrice={state?.include_price ?? true}
        loading={loading}
        errorMessage={errorMessage}
        compact={hasData}
        onLoad={handleLoadToken}
      />

      {hasData && (
        // Stage-1 retrofit: cross-fade on token switch instead of unmount.
        // Keeps the previous chart visible at 55% opacity while the next
        // dataset arrives, eliminating the skeleton-flash the audit flagged.
        <div
          style={{
            opacity: loading ? 0.55 : 1,
            transition: "opacity 200ms var(--ease-standard, ease)",
          }}
        >
          <LoadedView
            view={view}
            isDark={isDark}
            onUpdateFocus={handleUpdateFocus}
          />
        </div>
      )}
    </div>
    </MiniAppChrome>
  );
}
