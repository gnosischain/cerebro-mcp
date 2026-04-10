import { useEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import { useMiniApp } from "../shared/useMiniApp";
import { WarningBanner } from "../shared/WarningBanner";
import { AssistantBar } from "../shared/AssistantBar";
import type {
  DatasetDescriptor,
  MiniAppPayload,
} from "../shared/miniAppTypes";

interface TokenCatalogEntry {
  key: string;
  symbol: string;
  name: string;
  address: string;
  decimals: number;
  has_price: boolean;
}

type TokenMetric = "bridge_volume" | "bridge_txs" | "lp_count" | "price" | "holders" | "pool_tvl" | "pool_volume";
type TokenDirection = "inbound" | "outbound" | "both" | "";

interface TokenExplorerState {
  mode: "empty" | "loaded";
  token_catalog: TokenCatalogEntry[];
  selected_token: string;
  start_date: string;
  include_price: boolean;
  selected_metric: TokenMetric;
  bridge: string;
  direction: TokenDirection;
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

const MOCK_PAYLOAD: MiniAppPayload<TokenExplorerState> = {
  type: "INITIAL_LOAD",
  view_id: "dev-view",
  app_id: APP_ID,
  title: "Token Explorer",
  status: "ready",
  summary_cards: [
    { label: "Tokens available", value: String(MOCK_CATALOG.length), tone: "neutral" },
    { label: "Status", value: "Pick a token", tone: "neutral" },
  ],
  datasets: {},
  view_state: {
    mode: "empty",
    token_catalog: MOCK_CATALOG,
    selected_token: "",
    start_date: "2024-01-01",
    include_price: true,
    selected_metric: "bridge_volume",
    bridge: "",
    direction: "both",
  },
  warnings: [],
};

// ---------------------------------------------------------------------------
// Chart option builder (loaded mode)
// ---------------------------------------------------------------------------

function metricColumn(metric: TokenMetric): string {
  switch (metric) {
    case "bridge_volume":
      return "volume_usd";
    case "bridge_txs":
      return "txs";
    case "lp_count":
      return "unique_lp_count";
    case "price":
      return "price_usd";
    case "holders":
      return "holder_count";
    case "pool_tvl":
      return "tvl_usd";
    case "pool_volume":
      return "volume_usd";
  }
}

function datasetForMetric(
  metric: TokenMetric,
  datasets: Record<string, DatasetDescriptor> | undefined,
): DatasetDescriptor | undefined {
  if (!datasets) return undefined;
  switch (metric) {
    case "bridge_volume":
    case "bridge_txs":
      return datasets.bridge_flows;
    case "lp_count":
      return datasets.lp_counts;
    case "price":
      return datasets.price_history;
    case "holders":
      return datasets.holders;
    case "pool_tvl":
      return datasets.pool_tvl;
    case "pool_volume":
      return datasets.pool_volume;
  }
}

/** All metric tabs — only shown if the corresponding dataset exists. */
const ALL_METRICS: { key: TokenMetric; label: string; dsKey: string }[] = [
  { key: "bridge_volume", label: "Bridge vol.", dsKey: "bridge_flows" },
  { key: "bridge_txs", label: "Bridge txs", dsKey: "bridge_flows" },
  { key: "holders", label: "Holders", dsKey: "holders" },
  { key: "pool_tvl", label: "Pool TVL", dsKey: "pool_tvl" },
  { key: "pool_volume", label: "Pool volume", dsKey: "pool_volume" },
  { key: "price", label: "Price", dsKey: "price_history" },
  { key: "lp_count", label: "LPs", dsKey: "lp_counts" },
];

function buildEChartsOption(
  dataset: DatasetDescriptor | undefined,
  metricColumnName: string,
  bridgeFilter: string,
  direction: TokenDirection,
  isDark: boolean,
): Record<string, unknown> {
  if (!dataset || dataset.preview_rows.length === 0) {
    return { title: { text: "No data", left: "center", top: "center" } };
  }
  const dateIdx = dataset.columns.findIndex(
    (c) => c.name === "date" || c.name === "window",
  );
  const valueIdx = dataset.columns.findIndex((c) => c.name === metricColumnName);
  const bridgeIdx = dataset.columns.findIndex((c) => c.name === "bridge");
  const dirIdx = dataset.columns.findIndex((c) => c.name === "direction");
  if (valueIdx < 0) {
    return {
      title: {
        text: `Column '${metricColumnName}' not in dataset`,
        left: "center",
        top: "center",
      },
    };
  }
  let filteredRows = dataset.preview_rows;
  if (bridgeFilter && bridgeIdx >= 0) {
    filteredRows = filteredRows.filter((row) => row[bridgeIdx] === bridgeFilter);
  }
  if (direction && direction !== "both" && dirIdx >= 0) {
    filteredRows = filteredRows.filter((row) => row[dirIdx] === direction);
  }
  const xs = filteredRows.map((row) => String(row[dateIdx >= 0 ? dateIdx : 0] ?? ""));
  const ys = filteredRows.map((row) => Number(row[valueIdx] ?? 0));

  // Sort ascending by date for proper left-to-right chronological order
  if (xs.length > 0 && /^\d{4}-\d{2}/.test(xs[0])) {
    const paired = xs.map((x, i) => ({ x, y: ys[i] }));
    paired.sort((a, b) => a.x.localeCompare(b.x));
    for (let i = 0; i < paired.length; i++) {
      xs[i] = paired[i].x;
      ys[i] = paired[i].y;
    }
  }

  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    grid: { left: 60, right: 24, top: 36, bottom: 48 },
    xAxis: { type: "category", data: xs },
    yAxis: { type: "value" },
    series: [
      {
        type: "line",
        smooth: true,
        showSymbol: false,
        data: ys,
        lineStyle: { width: 2 },
        areaStyle: { opacity: isDark ? 0.18 : 0.12 },
      },
    ],
  };
}

// --- Basic analysis helpers (shared with MetricLab pattern) ---

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

function computeStats(
  rows: unknown[][],
  columns: string[],
): { name: string; count: number; min: number; max: number; mean: number; median: number }[] {
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
          <thead>
            <tr>
              <th>Column</th>
              <th>Count</th>
              <th>Min</th>
              <th>Mean</th>
              <th>Median</th>
              <th>Max</th>
            </tr>
          </thead>
          <tbody>
            {stats.map((s) => (
              <tr key={s.name}>
                <td>{s.name}</td>
                <td>{s.count.toLocaleString()}</td>
                <td>{fmtNum(s.min)}</td>
                <td>{fmtNum(s.mean)}</td>
                <td>{fmtNum(s.median)}</td>
                <td>{fmtNum(s.max)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Compact token picker (combobox + chip)
// ---------------------------------------------------------------------------

interface TokenPickerProps {
  catalog: TokenCatalogEntry[];
  initialStartDate: string;
  initialIncludePrice: boolean;
  loading: boolean;
  errorMessage: string | null;
  onLoad: (config: {
    symbol: string;
    startDate: string;
    includePrice: boolean;
  }) => void;
}

function TokenPicker({
  catalog,
  initialStartDate,
  initialIncludePrice,
  loading,
  errorMessage,
  onLoad,
}: TokenPickerProps) {
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<TokenCatalogEntry | null>(null);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [startDate, setStartDate] = useState(initialStartDate);
  const [includePrice, setIncludePrice] = useState(initialIncludePrice);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // Close dropdown on outside click.
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!wrapRef.current) return;
      if (!wrapRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const matches = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return catalog.slice(0, 12);
    return catalog
      .filter((t) => {
        const hay = `${t.symbol} ${t.name} ${t.address}`.toLowerCase();
        return hay.includes(q);
      })
      .slice(0, 12);
  }, [catalog, search]);

  useEffect(() => {
    if (selected && !selected.has_price) setIncludePrice(false);
  }, [selected]);

  const pick = (t: TokenCatalogEntry) => {
    setSelected(t);
    setSearch("");
    setDropdownOpen(false);
  };

  const handleLoad = () => {
    if (!selected) return;
    onLoad({ symbol: selected.symbol, startDate, includePrice });
  };

  return (
    <div className="mini-app-picker mini-app-picker--compact">
      <h2 className="mini-app-picker__title">Pick a token</h2>
      <p className="mini-app-picker__subtitle">
        {catalog.length.toLocaleString()} tokens in the registry. Type to
        search; pick one to load metadata, bridge flows, LPs, and price history.
      </p>

      {errorMessage && (
        <div className="mini-app-picker__error">{errorMessage}</div>
      )}

      <div className="mini-app-picker__compact-row">
        <div className="mini-app-picker__combo" ref={wrapRef}>
          <input
            type="search"
            className="mini-app-picker__combo-input"
            placeholder="Search token symbol, name, or address…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setDropdownOpen(true);
            }}
            onFocus={() => setDropdownOpen(true)}
          />
          <span className="mini-app-picker__count-pill">
            {catalog.length.toLocaleString()}
          </span>
          {dropdownOpen && matches.length > 0 && (
            <div className="mini-app-picker__dropdown">
              {matches.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  className="mini-app-picker__dropdown-item"
                  onMouseDown={(e) => {
                    e.preventDefault();
                    pick(t);
                  }}
                >
                  <strong>{t.symbol}</strong> — {t.name}
                </button>
              ))}
              {catalog.length > matches.length && (
                <div className="mini-app-picker__dropdown-more">
                  Keep typing to narrow down…
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="mini-app-picker__basket">
        {selected ? (
          <div className="mini-app-picker__chip">
            <strong>{selected.symbol}</strong>
            <span>{selected.name}</span>
            <button
              type="button"
              className="mini-app-picker__chip-remove"
              onClick={() => setSelected(null)}
              aria-label="Remove"
            >
              ×
            </button>
          </div>
        ) : (
          <div className="mini-app-picker__basket-empty">
            No token selected — pick one above.
          </div>
        )}
      </div>

      <details className="mini-app-picker__config">
        <summary>Options</summary>
        <fieldset className="mini-app-picker__fieldset--row">
          <label>
            Start date
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </label>
          <label className="mini-app-picker__checkbox">
            <input
              type="checkbox"
              checked={includePrice}
              onChange={(e) => setIncludePrice(e.target.checked)}
              disabled={!!selected && !selected.has_price}
            />
            Include 365-day price history
          </label>
        </fieldset>
      </details>

      <div className="mini-app-picker__actions">
        <button
          type="button"
          className="mini-app-picker__load-btn"
          onClick={handleLoad}
          disabled={loading || !selected}
        >
          {loading ? "Loading…" : "Load token"}
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
  sendMessage: (text: string) => Promise<boolean>;
  onSwitchToken: () => void;
  onUpdateFocus: (
    metric: TokenMetric,
    bridge: string,
    direction: TokenDirection,
  ) => void;
}

function LoadedView({
  view,
  isDark,
  sendMessage,
  onSwitchToken,
  onUpdateFocus,
}: LoadedViewProps) {
  const selectedMetric = view.view_state?.selected_metric ?? "bridge_volume";
  const bridgeFilter = view.view_state?.bridge ?? "";
  const direction = view.view_state?.direction ?? "both";

  // Only show metric tabs whose dataset actually has data.
  const availableTabs = useMemo(
    () => ALL_METRICS.filter((m) => {
      const ds = view.datasets?.[m.dsKey];
      return ds && ds.preview_rows.length > 0;
    }),
    [view.datasets],
  );

  const activeDS = datasetForMetric(selectedMetric, view.datasets);

  const chartOption = useMemo(
    () =>
      buildEChartsOption(
        activeDS,
        metricColumn(selectedMetric),
        bridgeFilter,
        direction,
        isDark,
      ),
    [activeDS, selectedMetric, bridgeFilter, direction, isDark],
  );

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

  const isBridgeMetric = selectedMetric === "bridge_volume" || selectedMetric === "bridge_txs";

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

      <section className="mini-app-controls">
        <button
          type="button"
          className="mini-app-picker__load-btn"
          onClick={onSwitchToken}
        >
          ← Pick a different token
        </button>
      </section>

      {/* Metric tabs — shown as a tab bar */}
      <div className="mini-app-analysis__tabs">
        {availableTabs.map((m) => (
          <button
            key={m.key}
            type="button"
            className={`mini-app-analysis__tab ${selectedMetric === m.key ? "is-active" : ""}`}
            onClick={() => onUpdateFocus(m.key, bridgeFilter, direction)}
          >
            {m.label}
          </button>
        ))}
      </div>

      {/* Bridge-specific filters (only when a bridge metric is active) */}
      {isBridgeMetric && (
        <section className="mini-app-controls">
          <fieldset>
            <legend>Bridge</legend>
            <select
              value={bridgeFilter}
              onChange={(e) =>
                onUpdateFocus(selectedMetric, e.target.value, direction)
              }
            >
              <option value="">All bridges</option>
              {bridgeOptions.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
          </fieldset>
          <fieldset>
            <legend>Direction</legend>
            {(["both", "inbound", "outbound"] as const).map((d) => (
              <label key={d}>
                <input
                  type="radio"
                  name="te-direction"
                  value={d}
                  checked={direction === d}
                  onChange={() => onUpdateFocus(selectedMetric, bridgeFilter, d)}
                />
                {d}
              </label>
            ))}
          </fieldset>
        </section>
      )}

      <section className="mini-app-chart">
        <ReactECharts
          option={chartOption}
          notMerge={true}
          style={{ height: 360, width: "100%" }}
          theme={isDark ? "dark" : undefined}
        />
      </section>

      {activeDS && activeDS.preview_rows.length > 0 && (
        <TokenAnalysisPanel
          rows={activeDS.preview_rows}
          columns={activeDS.columns.map((c) => c.name)}
        />
      )}

      <AssistantBar
        contextHint={view.view_state?.selected_token || "this token"}
        onSend={async (text) => {
          const s = view.view_state;
          const ctx = `[Token Explorer view_id=${view.view_id}, token=${s?.selected_token || "?"}, ` +
            `metric=${s?.selected_metric}, bridge=${s?.bridge || "all"}] ${text}`;
          return sendMessage(ctx);
        }}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

export default function TokenExplorerApp() {
  const { view, callTool, updateModelContext, sendMessage } =
    useMiniApp<TokenExplorerState>({
      appId: APP_ID,
      mockPayload: MOCK_PAYLOAD,
    });

  const [isDark, setIsDark] = useState(
    () => document.documentElement.dataset.theme !== "light",
  );
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
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

  useEffect(() => {
    setLoading(false);
    setErrorMessage(null);
    if (view?.view_state?.mode === "loaded") {
      setForceEmpty(false);
    }
  }, [view?.view_id, view?.view_state?.selected_token]);

  useEffect(() => {
    if (!view) return;
    const state = view.view_state;
    const bridgeStats = view.datasets?.bridge_flows?.stats;
    updateModelContext({
      view_id: view.view_id,
      mode: state?.mode ?? "empty",
      token: state?.selected_token || "n/a",
      selected_metric: state?.selected_metric ?? "n/a",
      bridge: state?.bridge ?? "",
      direction: state?.direction ?? "",
      dataset_mode: bridgeStats?.mode ?? "n/a",
      sample_source_rows: bridgeStats?.sample_source_rows ?? "n/a",
    });
  }, [view, updateModelContext]);

  const handleLoadToken = async (config: {
    symbol: string;
    startDate: string;
    includePrice: boolean;
  }) => {
    if (!view) return;
    setLoading(true);
    setErrorMessage(null);
    try {
      await callTool("load_token_explorer_token", {
        view_id: view.view_id,
        symbol_or_address: config.symbol,
        start_date: config.startDate,
        include_price: config.includePrice,
      });
      setForceEmpty(false);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateFocus = async (
    metric: TokenMetric,
    bridge: string,
    direction: TokenDirection,
  ) => {
    if (!view) return;
    try {
      await callTool("update_token_explorer_focus", {
        view_id: view.view_id,
        metric,
        bridge,
        direction,
      });
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : String(err));
    }
  };

  if (!view) {
    return <div className="mini-app-loading">Loading Token Explorer…</div>;
  }

  const state = view.view_state;
  const isEmptyMode =
    forceEmpty || !state || state.mode === "empty" || !view.datasets?.bridge_flows;

  return (
    <div className="mini-app-root mini-app-token-explorer">
      <header className="mini-app-header">
        <h1>{view.title}</h1>
        <span className="mini-app-subtitle">
          view_id: {view.view_id.slice(0, 8)}
        </span>
      </header>

      <WarningBanner warnings={view.warnings ?? []} />

      {isEmptyMode ? (
        <TokenPicker
          catalog={state?.token_catalog ?? []}
          initialStartDate={state?.start_date ?? "2024-01-01"}
          initialIncludePrice={state?.include_price ?? true}
          loading={loading}
          errorMessage={errorMessage}
          onLoad={handleLoadToken}
        />
      ) : (
        <LoadedView
          view={view}
          isDark={isDark}
          sendMessage={sendMessage}
          onSwitchToken={() => setForceEmpty(true)}
          onUpdateFocus={handleUpdateFocus}
        />
      )}
    </div>
  );
}
