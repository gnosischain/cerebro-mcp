import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import { useMiniApp } from "../shared/useMiniApp";
import { WarningBanner } from "../shared/WarningBanner";
import { AssistantBar } from "../shared/AssistantBar";
import type { DatasetMode, MiniAppPayload } from "../shared/miniAppTypes";

type ChartType =
  | "table"
  | "line"
  | "bar"
  | "scatter"
  | "heatmap"
  | "pie"
  | "numberDisplay";
type Aggregation = "count" | "sum" | "avg" | "min" | "max" | "median";

interface ChartConfig {
  xField: string;
  yField: string;
  chartType: ChartType;
  aggregation: Aggregation;
  groupBy: string;
}

interface MetricCatalogEntry {
  kind?: "metric" | "model";
  name: string;
  label: string;
  description: string;
  module: string;
  sector?: string;
  subsector?: string;
  root_model: string;
  quality_tier: string;
  unit: string;
  allowed_dimensions: string[];
  default_dimensions: string[];
  executable?: boolean;
  columns?: { name: string; type: string }[];
}

interface MetricLabState {
  mode: "empty" | "loaded";
  metric_catalog: MetricCatalogEntry[];
  catalog_query?: string;
  selected_metric: string;
  selected_metrics?: string[];
  selected_dimensions: string[];
  selected_limit: number;
  selected_order_by: string[];
  chart: ChartConfig;
  sort?: { field: string; direction: "asc" | "desc" };
  filters?: unknown[];
  analytics_disabled: boolean;
  estimates: boolean;
  dataset_mode: DatasetMode | null;
  sample_source_rows: number | null;
}

const APP_ID = "metric_lab";

const CHART_TYPES: ChartType[] = [
  "table",
  "line",
  "bar",
  "scatter",
  "heatmap",
  "pie",
  "numberDisplay",
];
const AGGREGATIONS: Aggregation[] = [
  "count",
  "sum",
  "avg",
  "min",
  "max",
  "median",
];

// ---------------------------------------------------------------------------
// Mock payload (dev mode)
// ---------------------------------------------------------------------------

const MOCK_CATALOG: MetricCatalogEntry[] = [
  {
    name: "execution_tx_count",
    label: "Execution transaction count",
    description: "Daily count of confirmed transactions on Gnosis Chain execution layer.",
    module: "execution",
    root_model: "int_execution_transactions_daily",
    quality_tier: "approved",
    unit: "count",
    allowed_dimensions: ["day", "week", "month", "client_version"],
    default_dimensions: ["day"],
  },
  {
    name: "bridge_volume_usd",
    label: "Bridge volume (USD)",
    description: "Daily bridge flow volume in USD across all Gnosis bridges.",
    module: "bridges",
    root_model: "int_bridges_flows_daily",
    quality_tier: "approved",
    unit: "USD",
    allowed_dimensions: ["day", "week", "bridge", "direction", "token"],
    default_dimensions: ["day", "bridge"],
  },
  {
    name: "validator_active_count",
    label: "Active validators",
    description: "Number of active validators per day on the Gnosis Beacon Chain.",
    module: "consensus",
    root_model: "int_consensus_validators_daily",
    quality_tier: "approved",
    unit: "count",
    allowed_dimensions: ["day", "status"],
    default_dimensions: ["day"],
  },
];

const MOCK_PAYLOAD: MiniAppPayload<MetricLabState> = {
  type: "INITIAL_LOAD",
  view_id: "dev-view",
  app_id: APP_ID,
  title: "Metric Lab",
  status: "ready",
  summary_cards: [
    { label: "Metrics available", value: String(MOCK_CATALOG.length), tone: "neutral" },
    { label: "Status", value: "Pick a metric", tone: "neutral" },
  ],
  datasets: {},
  view_state: {
    mode: "empty",
    metric_catalog: MOCK_CATALOG,
    catalog_query: "",
    selected_metric: "",
    selected_dimensions: [],
    selected_limit: 2000,
    selected_order_by: [],
    chart: {
      xField: "",
      yField: "",
      chartType: "table",
      aggregation: "sum",
      groupBy: "",
    },
    analytics_disabled: true,
    estimates: false,
    dataset_mode: null,
    sample_source_rows: null,
  },
  warnings: [],
};

// ---------------------------------------------------------------------------
// Client-side aggregation (for loaded mode)
// ---------------------------------------------------------------------------

function aggregateRows(
  rows: unknown[][],
  columns: string[],
  config: ChartConfig,
): { x: unknown[]; y: number[] } {
  const xIdx = columns.indexOf(config.xField);
  const yIdx = columns.indexOf(config.yField);
  if (xIdx < 0 || yIdx < 0) return { x: [], y: [] };

  if (config.aggregation === "count") {
    const counts = new Map<unknown, number>();
    for (const row of rows) {
      const key = row[xIdx];
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return { x: Array.from(counts.keys()), y: Array.from(counts.values()) };
  }

  const buckets = new Map<unknown, number[]>();
  for (const row of rows) {
    const key = row[xIdx];
    const value = Number(row[yIdx]);
    if (!Number.isFinite(value)) continue;
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key)!.push(value);
  }

  const reducer = (vals: number[]) => {
    if (vals.length === 0) return 0;
    switch (config.aggregation) {
      case "sum":
        return vals.reduce((a, b) => a + b, 0);
      case "avg":
        return vals.reduce((a, b) => a + b, 0) / vals.length;
      case "min":
        return Math.min(...vals);
      case "max":
        return Math.max(...vals);
      case "median": {
        const sorted = [...vals].sort((a, b) => a - b);
        const mid = Math.floor(sorted.length / 2);
        return sorted.length % 2 === 0
          ? (sorted[mid - 1] + sorted[mid]) / 2
          : sorted[mid];
      }
      default:
        return 0;
    }
  };

  return {
    x: Array.from(buckets.keys()),
    y: Array.from(buckets.values()).map(reducer),
  };
}

/** Sort parallel arrays ascending if labels look like dates (YYYY-MM-...). */
function sortDateAsc<T>(labels: string[], ...data: T[][]): void {
  if (labels.length === 0) return;
  if (!/^\d{4}-\d{2}/.test(labels[0])) return;
  const idx = labels.map((_, i) => i);
  idx.sort((a, b) => labels[a].localeCompare(labels[b]));
  const sortedLabels = idx.map((i) => labels[i]);
  const sortedData = data.map((arr) => idx.map((i) => arr[i]));
  for (let i = 0; i < labels.length; i++) {
    labels[i] = sortedLabels[i];
    for (let d = 0; d < data.length; d++) data[d][i] = sortedData[d][i];
  }
}

function buildChartOption(
  rows: unknown[][],
  columns: string[],
  config: ChartConfig,
  isDark: boolean,
  sortAsc: boolean = true,
): Record<string, unknown> {
  if (config.chartType === "table" || rows.length === 0) {
    return {};
  }
  const { x, y } = aggregateRows(rows, columns, config);
  const labels = x.map((v) => String(v));
  // Sort by date if applicable, then reverse if user wants descending.
  sortDateAsc(labels, y);
  if (!sortAsc) {
    labels.reverse();
    y.reverse();
  }

  const base = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    grid: { left: 60, right: 24, top: 36, bottom: 48 },
  };
  if (config.chartType === "pie") {
    return {
      backgroundColor: "transparent",
      tooltip: { trigger: "item" },
      series: [
        {
          type: "pie",
          radius: ["35%", "70%"],
          data: labels.map((name, i) => ({ name, value: y[i] })),
        },
      ],
    };
  }
  if (config.chartType === "scatter") {
    return {
      ...base,
      xAxis: { type: "category", data: labels },
      yAxis: { type: "value" },
      series: [{ type: "scatter", data: y }],
    };
  }
  if (config.chartType === "numberDisplay") {
    const total = y.reduce((a, b) => a + b, 0);
    return {
      title: {
        text: total.toLocaleString(),
        subtext: `${config.aggregation}(${config.yField})`,
        left: "center",
        top: "center",
      },
    };
  }
  return {
    ...base,
    xAxis: { type: "category", data: labels },
    yAxis: { type: "value" },
    series: [
      {
        type: config.chartType === "bar" ? "bar" : "line",
        smooth: config.chartType === "line",
        data: y,
        areaStyle:
          config.chartType === "line" ? { opacity: isDark ? 0.18 : 0.12 } : undefined,
      },
    ],
  };
}

/**
 * Dual-axis chart for multi-metric views. Each metric column gets its own
 * y-axis (first on left, rest on right). All share the x-axis (date).
 */
function buildMultiMetricChartOption(
  rows: unknown[][],
  columns: string[],
  xField: string,
  metricColumns: string[],
  isDark: boolean,
  sortAsc: boolean = true,
): Record<string, unknown> {
  const xIdx = columns.indexOf(xField);
  if (xIdx < 0 || metricColumns.length === 0) return {};
  const metricIdxs = metricColumns
    .map((name) => ({ name, idx: columns.indexOf(name) }))
    .filter((m) => m.idx >= 0);
  if (metricIdxs.length === 0) return {};

  // Collect unique x labels, build per-metric value maps
  const xLabels: string[] = [];
  const xSet = new Set<string>();
  const valMaps: Map<string, number>[] = metricIdxs.map(() => new Map());
  for (const row of rows) {
    const xVal = String(row[xIdx] ?? "");
    if (!xSet.has(xVal)) {
      xSet.add(xVal);
      xLabels.push(xVal);
    }
    for (let m = 0; m < metricIdxs.length; m++) {
      const v = Number(row[metricIdxs[m].idx] ?? 0);
      if (Number.isFinite(v)) valMaps[m].set(xVal, v);
    }
  }
  // Sort labels ascending if they look like dates, then reverse if needed.
  if (xLabels.length > 0 && /^\d{4}-\d{2}/.test(xLabels[0])) {
    xLabels.sort();
  }
  if (!sortAsc) xLabels.reverse();
  const dataSeries = metricIdxs.map((_, m) =>
    xLabels.map((x) => valMaps[m].get(x) ?? 0),
  );

  const colors = ["#818cf8", "#34d399", "#f97316", "#ec4899", "#06b6d4"];
  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: {
      bottom: 0,
      data: metricIdxs.map((m) => m.name),
      textStyle: { color: isDark ? "#94a3b8" : "#64748b" },
    },
    grid: { left: 60, right: metricIdxs.length > 1 ? 80 : 24, top: 36, bottom: 56 },
    xAxis: { type: "category", data: xLabels },
    yAxis: metricIdxs.map((m, i) => ({
      type: "value" as const,
      name: m.name,
      position: i === 0 ? "left" : "right",
      offset: i > 1 ? (i - 1) * 60 : 0,
      axisLine: { lineStyle: { color: colors[i % colors.length] } },
    })),
    series: metricIdxs.map((m, i) => ({
      name: m.name,
      type: "line",
      smooth: true,
      showSymbol: false,
      yAxisIndex: i,
      data: dataSeries[i],
      lineStyle: { width: 2, color: colors[i % colors.length] },
      itemStyle: { color: colors[i % colors.length] },
      areaStyle: i === 0 ? { opacity: isDark ? 0.12 : 0.08 } : undefined,
    })),
  };
}

// ---------------------------------------------------------------------------
// Analysis helpers (summary stats + Pearson correlation matrix)
// ---------------------------------------------------------------------------

interface ColumnStats {
  name: string;
  count: number;
  nulls: number;
  min: number;
  max: number;
  mean: number;
  median: number;
  stddev: number;
}

function isNumericColumn(rows: unknown[][], idx: number): boolean {
  let seen = 0;
  for (const row of rows) {
    const v = row[idx];
    if (v === null || v === undefined || v === "") continue;
    if (typeof v === "number") {
      seen++;
    } else if (typeof v === "string" && !isNaN(Number(v))) {
      seen++;
    } else {
      return false;
    }
    if (seen >= 10) return true;
  }
  return seen > 0;
}

function computeStats(rows: unknown[][], columns: string[]): ColumnStats[] {
  const out: ColumnStats[] = [];
  for (let idx = 0; idx < columns.length; idx++) {
    if (!isNumericColumn(rows, idx)) continue;
    const vals: number[] = [];
    let nulls = 0;
    for (const row of rows) {
      const v = row[idx];
      if (v === null || v === undefined || v === "") {
        nulls++;
        continue;
      }
      const n = Number(v);
      if (Number.isFinite(n)) vals.push(n);
      else nulls++;
    }
    if (vals.length === 0) continue;
    const sorted = [...vals].sort((a, b) => a - b);
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    const variance =
      vals.reduce((a, b) => a + (b - mean) ** 2, 0) / vals.length;
    const mid = Math.floor(sorted.length / 2);
    const median =
      sorted.length % 2 === 0
        ? (sorted[mid - 1] + sorted[mid]) / 2
        : sorted[mid];
    out.push({
      name: columns[idx],
      count: vals.length,
      nulls,
      min: sorted[0],
      max: sorted[sorted.length - 1],
      mean,
      median,
      stddev: Math.sqrt(variance),
    });
  }
  return out;
}

function computeCorrelationMatrix(
  rows: unknown[][],
  columns: string[],
): { cols: string[]; matrix: number[][] } {
  const numericIdx: number[] = [];
  for (let i = 0; i < columns.length; i++) {
    if (isNumericColumn(rows, i)) numericIdx.push(i);
  }
  const vectors: number[][] = numericIdx.map(() => []);
  for (const row of rows) {
    let allValid = true;
    const tmp: number[] = [];
    for (const idx of numericIdx) {
      const n = Number(row[idx]);
      if (!Number.isFinite(n)) {
        allValid = false;
        break;
      }
      tmp.push(n);
    }
    if (!allValid) continue;
    for (let k = 0; k < tmp.length; k++) vectors[k].push(tmp[k]);
  }
  const means = vectors.map(
    (v) => v.reduce((a, b) => a + b, 0) / (v.length || 1),
  );
  const stds = vectors.map((v, i) => {
    const mean = means[i];
    const variance =
      v.reduce((a, b) => a + (b - mean) ** 2, 0) / (v.length || 1);
    return Math.sqrt(variance);
  });
  const n = numericIdx.length;
  const matrix: number[][] = Array.from({ length: n }, () => Array(n).fill(0));
  for (let i = 0; i < n; i++) {
    for (let j = i; j < n; j++) {
      if (i === j) {
        matrix[i][j] = 1;
        continue;
      }
      const denom = stds[i] * stds[j];
      if (denom === 0) {
        matrix[i][j] = 0;
        matrix[j][i] = 0;
        continue;
      }
      let cov = 0;
      const len = Math.min(vectors[i].length, vectors[j].length);
      for (let k = 0; k < len; k++) {
        cov += (vectors[i][k] - means[i]) * (vectors[j][k] - means[j]);
      }
      cov /= len || 1;
      const r = cov / denom;
      matrix[i][j] = r;
      matrix[j][i] = r;
    }
  }
  return { cols: numericIdx.map((i) => columns[i]), matrix };
}

function fmtNum(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(2) + "k";
  if (Math.abs(n) < 0.01 && n !== 0) return n.toExponential(2);
  return n.toFixed(2);
}

function corrColor(r: number): string {
  // Red (neg) to white (0) to blue (pos)
  const a = Math.min(1, Math.abs(r));
  if (r >= 0) {
    return `rgba(99, 179, 237, ${a * 0.55})`;
  }
  return `rgba(252, 129, 129, ${a * 0.55})`;
}

interface AnalysisPanelProps {
  rows: unknown[][];
  columns: string[];
}

function AnalysisPanel({ rows, columns }: AnalysisPanelProps) {
  const [tab, setTab] = useState<"summary" | "corr">("summary");
  const stats = useMemo(() => computeStats(rows, columns), [rows, columns]);
  const corr = useMemo(
    () => computeCorrelationMatrix(rows, columns),
    [rows, columns],
  );

  if (stats.length === 0) {
    return (
      <div className="mini-app-analysis">
        <div className="mini-app-picker__empty">
          No numeric columns detected — analysis tools are unavailable for
          this dataset.
        </div>
      </div>
    );
  }

  return (
    <div className="mini-app-analysis">
      <div className="mini-app-analysis__tabs">
        <button
          type="button"
          className={`mini-app-analysis__tab ${tab === "summary" ? "is-active" : ""}`}
          onClick={() => setTab("summary")}
        >
          Summary ({stats.length})
        </button>
        <button
          type="button"
          className={`mini-app-analysis__tab ${tab === "corr" ? "is-active" : ""}`}
          onClick={() => setTab("corr")}
          disabled={corr.cols.length < 2}
          title={corr.cols.length < 2 ? "Need ≥2 numeric columns" : ""}
        >
          Correlations ({corr.cols.length}×{corr.cols.length})
        </button>
      </div>

      {tab === "summary" && (
        <div className="mini-app-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Column</th>
                <th>Count</th>
                <th>Nulls</th>
                <th>Min</th>
                <th>Median</th>
                <th>Mean</th>
                <th>Max</th>
                <th>Stddev</th>
              </tr>
            </thead>
            <tbody>
              {stats.map((s) => (
                <tr key={s.name}>
                  <td>{s.name}</td>
                  <td>{s.count.toLocaleString()}</td>
                  <td>{s.nulls.toLocaleString()}</td>
                  <td>{fmtNum(s.min)}</td>
                  <td>{fmtNum(s.median)}</td>
                  <td>{fmtNum(s.mean)}</td>
                  <td>{fmtNum(s.max)}</td>
                  <td>{fmtNum(s.stddev)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "corr" && corr.cols.length >= 2 && (
        <div className="mini-app-table-wrap">
          <table>
            <thead>
              <tr>
                <th></th>
                {corr.cols.map((c) => (
                  <th key={c} title={c}>
                    {c.length > 14 ? c.slice(0, 12) + "…" : c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {corr.cols.map((rowCol, i) => (
                <tr key={rowCol}>
                  <td>{rowCol}</td>
                  {corr.matrix[i].map((r, j) => (
                    <td
                      key={j}
                      className="mini-app-corr-cell"
                      style={{ background: corrColor(r) }}
                      title={`${rowCol} × ${corr.cols[j]}: r = ${r.toFixed(4)}`}
                    >
                      {r.toFixed(2)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Metric picker (shown when mode === "empty")
// ---------------------------------------------------------------------------

interface MetricPickerProps {
  catalog: MetricCatalogEntry[];
  onLoad: (config: {
    metrics: string[];
    dimensions: string[];
    limit: number;
    orderBy: string[];
  }) => void;
  loading: boolean;
  errorMessage: string | null;
}

/**
 * Compact picker: single searchable combobox (no wall of list items)
 * + chip basket for multi-select + collapsed config panel.
 */
function MetricPicker({ catalog, onLoad, loading, errorMessage }: MetricPickerProps) {
  const [search, setSearch] = useState("");
  const [sector, setSector] = useState<string>("");
  const [subsector, setSubsector] = useState<string>("");
  const [basket, setBasket] = useState<string[]>([]);
  const [dimensions, setDimensions] = useState<string[]>([]);
  const [limit, setLimit] = useState<number>(2000);
  const [orderByField, setOrderByField] = useState<string>("");
  const [orderDir, setOrderDir] = useState<"asc" | "desc">("desc");
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [showConfig, setShowConfig] = useState(false);

  const sectors = useMemo(() => {
    const set = new Set<string>();
    for (const m of catalog) if (m.sector) set.add(m.sector);
    return Array.from(set).sort();
  }, [catalog]);

  const subsectors = useMemo(() => {
    if (!sector) return [] as string[];
    const set = new Set<string>();
    for (const m of catalog) {
      if (m.sector === sector && m.subsector) set.add(m.subsector);
    }
    return Array.from(set).sort();
  }, [catalog, sector]);

  useEffect(() => {
    setSubsector("");
  }, [sector]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return catalog.filter((m) => {
      if (sector && m.sector !== sector) return false;
      if (subsector && m.subsector !== subsector) return false;
      if (!q) return true;
      const hay = `${m.name} ${m.label} ${m.description}`.toLowerCase();
      return hay.includes(q);
    });
  }, [catalog, search, sector, subsector]);

  const suggestions = useMemo(() => filtered.slice(0, 10), [filtered]);

  const basketEntries = useMemo(
    () =>
      basket
        .map((name) => catalog.find((m) => m.name === name))
        .filter((m): m is MetricCatalogEntry => Boolean(m)),
    [basket, catalog],
  );

  // Treat the first basket entry as the "anchor" for dimension/order options.
  const anchor = basketEntries[0] ?? null;
  const hasModel = basketEntries.some((m) => m.kind === "model");
  const canMultiSelect = !hasModel;

  // Reset dimensions when the anchor changes
  useEffect(() => {
    if (!anchor) {
      setDimensions([]);
      setOrderByField("");
      return;
    }
    setDimensions(anchor.default_dimensions.slice());
    setOrderByField("");
  }, [anchor?.name]);

  const addToBasket = (name: string) => {
    const entry = catalog.find((m) => m.name === name);
    if (!entry) return;
    if (entry.kind === "model") {
      // api_* tables: allow up to 2 for dual-axis comparison.
      // Mixing tables with semantic metrics is not supported.
      if (hasModel || basket.length === 0) {
        setBasket((prev) => {
          if (prev.includes(name)) return prev;
          if (prev.length >= 2) return [prev[prev.length - 1], name]; // keep last + new
          return [...prev, name];
        });
      } else {
        // switching from metrics to a table — replace
        setBasket([name]);
      }
    } else if (hasModel) {
      // switching from a table to metrics — replace
      setBasket([name]);
    } else {
      setBasket((prev) => (prev.includes(name) ? prev : [...prev, name]));
    }
    setSearch("");
    setDropdownOpen(false);
  };

  const removeFromBasket = (name: string) => {
    setBasket((prev) => prev.filter((n) => n !== name));
  };

  const toggleDimension = (dim: string) => {
    setDimensions((prev) =>
      prev.includes(dim) ? prev.filter((d) => d !== dim) : [...prev, dim],
    );
  };

  const handleLoad = () => {
    if (basket.length === 0) return;
    const orderBy = orderByField ? [`${orderByField} ${orderDir}`] : [];
    onLoad({
      metrics: basket,
      dimensions,
      limit,
      orderBy,
    });
  };

  return (
    <div className="mini-app-picker mini-app-picker--compact">
      <div className="mini-app-picker__head">
        <h2 className="mini-app-picker__title">Build a query</h2>
        <span className="mini-app-picker__hint">
          {catalog.length.toLocaleString()} metrics & tables · search by name,
          sector, or description
        </span>
      </div>

      {errorMessage && (
        <div className="mini-app-picker__error">{errorMessage}</div>
      )}

      {/* Compact filter row */}
      <div className="mini-app-picker__compact-row">
        <select
          value={sector}
          onChange={(e) => setSector(e.target.value)}
          className="mini-app-picker__chip-select"
          title="Sector"
        >
          <option value="">all sectors</option>
          {sectors.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          value={subsector}
          onChange={(e) => setSubsector(e.target.value)}
          disabled={!sector || subsectors.length === 0}
          className="mini-app-picker__chip-select"
          title="Subsector"
        >
          <option value="">all subsectors</option>
          {subsectors.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        {/* Combobox with inline dropdown */}
        <div className="mini-app-picker__combo">
          <input
            type="search"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setDropdownOpen(true);
            }}
            onFocus={() => setDropdownOpen(true)}
            onBlur={() => setTimeout(() => setDropdownOpen(false), 160)}
            placeholder={
              basket.length === 0
                ? "Search to add a metric or table…"
                : hasModel
                  ? "Replace table…"
                  : "Add another metric…"
            }
            className="mini-app-picker__combo-input"
          />
          <span className="mini-app-picker__count-pill">
            {filtered.length}
          </span>
          {dropdownOpen && suggestions.length > 0 && (
            <div className="mini-app-picker__dropdown">
              {suggestions.map((m) => (
                <button
                  key={m.name}
                  type="button"
                  className="mini-app-picker__dropdown-item"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => addToBasket(m.name)}
                  disabled={basket.includes(m.name)}
                >
                  <span className="mini-app-picker__kind-pill">
                    {m.kind === "model" ? "tbl" : "mtr"}
                  </span>
                  <span className="mini-app-picker__dropdown-label">
                    {m.label}
                  </span>
                  <span className="mini-app-picker__dropdown-path">
                    {[m.sector, m.subsector].filter(Boolean).join(" › ")}
                  </span>
                </button>
              ))}
              {filtered.length > suggestions.length && (
                <div className="mini-app-picker__dropdown-more">
                  + {filtered.length - suggestions.length} more — keep typing
                  to narrow
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Basket — selected metric chips */}
      <div className="mini-app-picker__basket">
        {basket.length === 0 ? (
          <span className="mini-app-picker__basket-empty">
            No metrics selected yet.
          </span>
        ) : (
          basketEntries.map((m, i) => (
            <span key={m.name} className="mini-app-picker__chip">
              <span className="mini-app-picker__kind-pill">
                {m.kind === "model" ? "tbl" : "mtr"}
              </span>
              {m.label}
              {i === 0 && basketEntries.length > 1 && (
                <span
                  className="mini-app-picker__hint"
                  title="Anchor metric — its dimensions drive the query"
                >
                  anchor
                </span>
              )}
              <button
                type="button"
                className="mini-app-picker__chip-remove"
                onClick={() => removeFromBasket(m.name)}
                title="Remove"
              >
                ×
              </button>
            </span>
          ))
        )}
        {canMultiSelect && basket.length >= 1 && (
          <span className="mini-app-picker__hint">
            Semantic metrics compose into one query — add more for side-by-side
            comparison & correlations.
          </span>
        )}
        {hasModel && basket.length < 2 && (
          <span className="mini-app-picker__hint">
            Add a second table for dual-axis comparison (up to 2).
          </span>
        )}
        {hasModel && basket.length >= 2 && (
          <span className="mini-app-picker__hint">
            Two tables selected — they will be plotted on separate y-axes.
          </span>
        )}
      </div>

      {/* Collapsed config panel */}
      <details
        className="mini-app-picker__config"
        open={showConfig}
        onToggle={(e) => setShowConfig((e.target as HTMLDetailsElement).open)}
      >
        <summary>
          Options
          {anchor && (
            <>
              {" · "}
              {dimensions.length} dim{dimensions.length !== 1 && "s"}
              {" · "}
              limit {limit}
              {orderByField && ` · order ${orderByField} ${orderDir}`}
            </>
          )}
        </summary>

        {anchor && anchor.kind !== "model" && anchor.allowed_dimensions.length > 0 && (
          <fieldset className="mini-app-picker__fieldset">
            <legend>Dimensions (for {anchor.label})</legend>
            <div className="mini-app-picker__dim-row">
              {anchor.allowed_dimensions.map((dim) => (
                <label key={dim} className="mini-app-picker__checkbox">
                  <input
                    type="checkbox"
                    checked={dimensions.includes(dim)}
                    onChange={() => toggleDimension(dim)}
                  />
                  {dim}
                </label>
              ))}
            </div>
          </fieldset>
        )}

        <fieldset className="mini-app-picker__fieldset mini-app-picker__fieldset--row">
          <label>
            <span>Order by</span>
            <select
              value={orderByField}
              onChange={(e) => setOrderByField(e.target.value)}
            >
              <option value="">— none —</option>
              {(anchor?.allowed_dimensions ?? []).map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Direction</span>
            <select
              value={orderDir}
              onChange={(e) => setOrderDir(e.target.value as "asc" | "desc")}
              disabled={!orderByField}
            >
              <option value="desc">desc</option>
              <option value="asc">asc</option>
            </select>
          </label>
          <label>
            <span>Row limit</span>
            <input
              type="number"
              min={10}
              max={20000}
              step={100}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value) || 2000)}
            />
          </label>
        </fieldset>

        {anchor?.description && (
          <p className="mini-app-picker__description">{anchor.description}</p>
        )}
      </details>

      <div className="mini-app-picker__actions">
        <button
          type="button"
          className="mini-app-picker__load-btn"
          onClick={handleLoad}
          disabled={loading || basket.length === 0}
        >
          {loading
            ? "Loading…"
            : basket.length > 1
              ? `Load ${basket.length} metrics`
              : "Load"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Loaded view (table + chart config + chart)
// ---------------------------------------------------------------------------

interface LoadedViewProps {
  view: MiniAppPayload<MetricLabState>;
  rows: unknown[][];
  columns: string[];
  chartConfig: ChartConfig;
  selectedMetrics: string[];
  previewOnly: boolean;
  estimates: boolean;
  isDark: boolean;
  callTool: (name: string, args: Record<string, unknown>) => Promise<unknown>;
  sendMessage: (text: string) => Promise<boolean>;
  onSwitchMetric: () => void;
}

function LoadedView({
  view,
  rows,
  columns,
  chartConfig,
  selectedMetrics,
  previewOnly,
  estimates,
  isDark,
  callTool,
  sendMessage,
  onSwitchMetric,
}: LoadedViewProps) {
  // Local chart state for instant responsiveness; syncs from server on prop change.
  const [localChart, setLocalChart] = useState<ChartConfig>(chartConfig);
  const [sortAsc, setSortAsc] = useState(true);
  useEffect(() => setLocalChart(chartConfig), [chartConfig]);

  const updateChart = (patch: Partial<ChartConfig>) => {
    const next = { ...localChart, ...patch };
    setLocalChart(next);
    // Fire-and-forget server sync so LLM model context stays current.
    callTool("update_metric_lab_chart", {
      view_id: view.view_id,
      x_field: next.xField,
      y_field: next.yField,
      chart_type: next.chartType,
      aggregation: next.aggregation,
    }).catch(() => {});
  };

  const isMultiMetric = selectedMetrics.length > 1;
  const hasDualDatasets = !!view.datasets?.secondary;

  // For dual-table mode: merge primary + secondary datasets into a combined
  // row set keyed by the shared date column, with numeric columns renamed
  // to include the table name for disambiguation.
  const { mergedRows, mergedColumns, mergedMetricCols } = useMemo(() => {
    if (!hasDualDatasets || !view.datasets?.primary || !view.datasets?.secondary) {
      return { mergedRows: rows, mergedColumns: columns, mergedMetricCols: selectedMetrics };
    }
    const p = view.datasets.primary;
    const s = view.datasets.secondary;
    const pCols = p.columns.map((c) => c.name);
    const sCols = s.columns.map((c) => c.name);
    // Find shared date column.
    const dateCol = pCols.find((c) => /^(date|day|week|month)$/i.test(c)) ?? pCols[0];
    const pDateIdx = pCols.indexOf(dateCol);
    const sDateIdx = sCols.indexOf(dateCol);
    if (pDateIdx < 0 || sDateIdx < 0) {
      return { mergedRows: rows, mergedColumns: columns, mergedMetricCols: selectedMetrics };
    }
    // Find first numeric column in each dataset (skip the date column).
    const pValIdx = pCols.findIndex((_c, i) => i !== pDateIdx);
    const sValIdx = sCols.findIndex((_c, i) => i !== sDateIdx);
    const pLabel = selectedMetrics[0]?.replace("api_", "").replace(/_/g, " ") ?? "primary";
    const sLabel = selectedMetrics[1]?.replace("api_", "").replace(/_/g, " ") ?? "secondary";
    // Build a date→values map.
    const map = new Map<string, [unknown, unknown]>();
    for (const row of p.preview_rows) {
      const d = String(row[pDateIdx] ?? "");
      map.set(d, [row[pValIdx] ?? 0, null]);
    }
    for (const row of s.preview_rows) {
      const d = String(row[sDateIdx] ?? "");
      const existing = map.get(d);
      if (existing) existing[1] = row[sValIdx] ?? 0;
      else map.set(d, [null, row[sValIdx] ?? 0]);
    }
    const mCols = [dateCol, pLabel, sLabel];
    const mRows: unknown[][] = [];
    for (const [d, [pv, sv]] of map) {
      mRows.push([d, pv ?? 0, sv ?? 0]);
    }
    return { mergedRows: mRows, mergedColumns: mCols, mergedMetricCols: [pLabel, sLabel] };
  }, [hasDualDatasets, view.datasets, rows, columns, selectedMetrics]);

  const chartOption = useMemo(() => {
    if ((isMultiMetric || hasDualDatasets) && localChart.chartType !== "table") {
      return buildMultiMetricChartOption(
        mergedRows, mergedColumns, mergedColumns[0] ?? localChart.xField,
        mergedMetricCols, isDark, sortAsc,
      );
    }
    return buildChartOption(rows, columns, localChart, isDark, sortAsc);
  }, [rows, columns, mergedRows, mergedColumns, mergedMetricCols, localChart, isMultiMetric, hasDualDatasets, isDark, sortAsc]);

  return (
    <>
      <section className="mini-app-summary-grid">
        {(view.summary_cards ?? []).map((card, i) => (
          <div
            key={i}
            className={`mini-app-summary-card tone-${card.tone ?? "neutral"}`}
          >
            <div className="mini-app-summary-label">{card.label}</div>
            <div className="mini-app-summary-value">{card.value}</div>
            {card.delta && <div className="mini-app-summary-delta">{card.delta}</div>}
          </div>
        ))}
      </section>

      <section className="mini-app-chart-config">
        <button
          type="button"
          className="mini-app-picker__load-btn"
          onClick={onSwitchMetric}
        >
          ← Pick a different metric
        </button>
        <label>
          chart
          <select
            value={localChart.chartType}
            disabled={previewOnly}
            onChange={(e) => updateChart({ chartType: e.target.value as ChartType })}
          >
            {CHART_TYPES.map((c) => (
              <option key={c} value={c} disabled={previewOnly && c !== "table"}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label>
          x
          <select
            value={localChart.xField}
            onChange={(e) => updateChart({ xField: e.target.value })}
          >
            {columns.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label>
          y
          <select
            value={localChart.yField}
            onChange={(e) => updateChart({ yField: e.target.value })}
          >
            {columns.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label>
          aggregation
          <select
            value={localChart.aggregation}
            disabled={previewOnly}
            onChange={(e) => updateChart({ aggregation: e.target.value as Aggregation })}
          >
            {AGGREGATIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="mini-app-picker__load-btn"
          onClick={() => updateChart({ xField: localChart.yField, yField: localChart.xField })}
          title="Swap X and Y axes"
        >
          x↔y
        </button>
        <button
          type="button"
          className="mini-app-picker__load-btn"
          onClick={() => setSortAsc((v) => !v)}
          title="Toggle sort direction"
        >
          {sortAsc ? "asc ↑" : "desc ↓"}
        </button>
        {estimates && (
          <span className="mini-app-pill mini-app-pill--estimate">≈ estimate</span>
        )}
        {previewOnly && (
          <span className="mini-app-pill mini-app-pill--warning">
            analytics disabled
          </span>
        )}
      </section>

      <section className="mini-app-chart">
        {localChart.chartType === "table" ? (
          <div className="mini-app-table-wrap">
            <table>
              <thead>
                <tr>
                  {columns.map((c) => (
                    <th key={c}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 200).map((row, i) => (
                  <tr key={i}>
                    {row.map((cell, j) => (
                      <td key={j}>
                        {cell === null || cell === undefined ? "" : String(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > 200 && (
              <div className="mini-app-table-footer">
                Showing first 200 of {rows.length.toLocaleString()} rows
              </div>
            )}
          </div>
        ) : (
          <ReactECharts
            option={chartOption}
            notMerge={true}
            style={{ height: 420, width: "100%" }}
            theme={isDark ? "dark" : undefined}
          />
        )}
      </section>

      <AnalysisPanel rows={rows} columns={columns} />

      <AssistantBar
        contextHint={selectedMetrics.join(", ") || "this dataset"}
        onSend={async (text) => {
          const ctx = `[Metric Lab view_id=${view.view_id}, metrics=${selectedMetrics.join(",")}, ` +
            `chart=${localChart.chartType}, x=${localChart.xField}, y=${localChart.yField}, ` +
            `rows=${rows.length}] ${text}`;
          return sendMessage(ctx);
        }}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Root app
// ---------------------------------------------------------------------------

export default function MetricLabApp() {
  const { view, fetchRows, callTool, updateModelContext, sendMessage } =
    useMiniApp<MetricLabState>({
      appId: APP_ID,
      mockPayload: MOCK_PAYLOAD,
    });

  const [hydratedRows, setHydratedRows] = useState<unknown[][] | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isDark, setIsDark] = useState(
    () => document.documentElement.dataset.theme !== "light",
  );
  const [forceEmpty, setForceEmpty] = useState(false);

  useEffect(() => {
    const obs = new MutationObserver(() => {
      setIsDark(document.documentElement.dataset.theme !== "light");
    });
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => obs.disconnect();
  }, []);

  // Whenever a fresh payload arrives, reset hydration cache + loading state.
  useEffect(() => {
    setHydratedRows(null);
    setLoading(false);
    setErrorMessage(null);
    if (view?.view_state?.mode === "loaded") {
      setForceEmpty(false);
    }
  }, [view?.view_id, view?.view_state?.selected_metric]);

  // Hydrate the rest of the dataset (up to 5,000 rows buffered).
  useEffect(() => {
    if (!view) return;
    const primary = view.datasets?.primary;
    if (!primary) return;
    setHydratedRows(primary.preview_rows);

    let cancelled = false;
    const buffered: unknown[][] = [...primary.preview_rows];
    const cap = 5000;

    const pump = async (token: string) => {
      while (token && buffered.length < cap && !cancelled) {
        const page = await fetchRows(view.view_id, "primary", token);
        if (!page) break;
        for (const row of page.rows) buffered.push(row);
        token = page.next_page_token;
      }
      // Only update state once pumping is complete to avoid chart flicker.
      if (!cancelled) setHydratedRows([...buffered]);
    };

    if (primary.page_token) {
      void pump(primary.page_token);
    }

    return () => {
      cancelled = true;
    };
  }, [view, fetchRows]);

  useEffect(() => {
    if (!view) return;
    const state = view.view_state;
    if (!state) return;
    updateModelContext({
      view_id: view.view_id,
      mode: state.mode,
      selected_metric: state.selected_metric || "n/a",
      dataset_mode: state.dataset_mode ?? "n/a",
      sample_source_rows: state.sample_source_rows ?? "n/a",
      chart: state.chart?.chartType ?? "n/a",
      x: state.chart?.xField ?? "n/a",
      y: state.chart?.yField ?? "n/a",
    });
  }, [view, updateModelContext]);

  const handleLoadMetric = async (config: {
    metrics: string[];
    dimensions: string[];
    limit: number;
    orderBy: string[];
  }) => {
    if (!view) return;
    setLoading(true);
    setErrorMessage(null);
    try {
      await callTool("load_metric_lab_metric", {
        view_id: view.view_id,
        // Backend accepts either str or list[str]. Pass a single string
        // for one metric so older clients / API surfaces stay happy.
        metric: config.metrics.length === 1 ? config.metrics[0] : config.metrics,
        dimensions: config.dimensions,
        order_by: config.orderBy,
        limit: config.limit,
      });
      // Successful load — clear the "forced empty" override so the
      // loaded view can render the new dataset.
      setForceEmpty(false);
      setHydratedRows(null);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  if (!view) {
    return <div className="mini-app-loading">Loading Metric Lab…</div>;
  }

  const state = view.view_state;
  const isEmptyMode = forceEmpty || !state || state.mode === "empty" || !view.datasets?.primary;

  return (
    <div className="mini-app-root mini-app-metric-lab">
      <header className="mini-app-header">
        <h1>{view.title}</h1>
        <span className="mini-app-subtitle">
          view_id: {view.view_id.slice(0, 8)}
        </span>
      </header>

      <WarningBanner warnings={view.warnings ?? []} />

      {isEmptyMode ? (
        <MetricPicker
          catalog={state?.metric_catalog ?? []}
          onLoad={handleLoadMetric}
          loading={loading}
          errorMessage={errorMessage}
        />
      ) : (
        <LoadedView
          view={view}
          rows={hydratedRows ?? view.datasets?.primary?.preview_rows ?? []}
          columns={view.datasets?.primary?.columns.map((c) => c.name) ?? []}
          chartConfig={state!.chart}
          selectedMetrics={state!.selected_metrics ?? (state!.selected_metric ? [state!.selected_metric] : [])}
          previewOnly={state!.analytics_disabled === true}
          estimates={state!.estimates === true}
          isDark={isDark}
          callTool={callTool}
          sendMessage={sendMessage}
          onSwitchMetric={() => setForceEmpty(true)}
        />
      )}
    </div>
  );
}
