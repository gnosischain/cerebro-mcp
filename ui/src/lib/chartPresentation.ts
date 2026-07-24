import type { EChartsOption } from "echarts";

/**
 * Report-surface chart presentation pass.
 *
 * Chart specs arrive from the server as bare JSON (no functions), built by
 * generic SQL->ECharts builders: raw column names as series/legend text, raw
 * "150,000,000" value-axis labels, raw "2023-06-01" category ticks. This
 * module upgrades a spec to editorial quality at render time:
 *
 *  - compact value-axis labels (1.5K / 2.3M / 1.2B, `$`-prefixed when the
 *    chart is confidently currency-denominated)
 *  - locale-formatted tooltip values via `tooltip.valueFormatter`
 *  - date-aware category ticks (years for multi-year spans, months below)
 *  - humanized snake_case series / legend / axis names
 *  - line polish (no per-point symbols on dense series, gentler smoothing)
 *  - scatter polish (axis titles placed mid-axis; one-point-per-series
 *    scatters become direct-labeled point clouds with no legend)
 *  - single-series cartesian charts drop the legend (the card title names it)
 *
 * It never overrides values an author set explicitly beyond the known
 * builder defaults, and it operates on a deep clone — the incoming spec
 * (shared via report data) is not mutated. Old saved reports benefit
 * retroactively since the pass runs client-side.
 */

// ---------------------------------------------------------------------------
// Value formatting
// ---------------------------------------------------------------------------

export function formatCompact(value: number, currency = false): string {
  if (value == null || Number.isNaN(value)) return "";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  const prefix = currency ? "$" : "";
  const scale = (div: number, suffix: string) => {
    const scaled = abs / div;
    const digits = scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2;
    const text = scaled.toFixed(digits).replace(/\.0+$/, "").replace(/(\.\d*[1-9])0+$/, "$1");
    return `${sign}${prefix}${text}${suffix}`;
  };
  if (abs >= 1e12) return scale(1e12, "T");
  if (abs >= 1e9) return scale(1e9, "B");
  if (abs >= 1e6) return scale(1e6, "M");
  if (abs >= 1e3) return scale(1e3, "K");
  if (abs === 0) return `${prefix}0`;
  if (abs < 0.01) return `${sign}${prefix}${abs.toExponential(1)}`;
  const digits = Number.isInteger(value) ? 0 : abs < 1 ? 3 : 2;
  return `${sign}${prefix}${abs.toFixed(digits).replace(/\.0+$/, "")}`;
}

export function formatFull(value: unknown, currency = false): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return value == null ? "—" : String(value);
  }
  const prefix = currency ? "$" : "";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  const opts: Intl.NumberFormatOptions =
    abs >= 100
      ? { maximumFractionDigits: 0 }
      : abs >= 1
        ? { maximumFractionDigits: 2 }
        : { maximumSignificantDigits: 3 };
  return `${sign}${prefix}${abs.toLocaleString("en-US", opts)}`;
}

// ---------------------------------------------------------------------------
// Name humanization
// ---------------------------------------------------------------------------

const UNIT_SUFFIXES: Record<string, string> = {
  usd: "USD",
  eur: "EUR",
  eth: "ETH",
  xdai: "xDAI",
  pct: "%",
};

/** "payment_volume_usd" -> "Payment volume (USD)"; leaves real-world names
 * ("EURe", "USD-pegged", "sDAI") untouched. */
export function humanizeName(raw: string): string {
  if (!raw) return raw;
  // snake_case column names only: all-lowercase alnum with >=1 underscore
  if (/^[a-z][a-z0-9]*(_[a-z0-9]+)+$/.test(raw)) {
    const parts = raw.split("_");
    let unit = "";
    const last = parts[parts.length - 1];
    if (UNIT_SUFFIXES[last] && parts.length > 1) {
      unit = UNIT_SUFFIXES[last];
      parts.pop();
    }
    const label = parts.join(" ");
    const capped = label.charAt(0).toUpperCase() + label.slice(1);
    return unit ? `${capped} (${unit})` : capped;
  }
  // single lowercase word: capitalize ("holders" -> "Holders")
  if (/^[a-z][a-z0-9]*$/.test(raw)) {
    return raw.charAt(0).toUpperCase() + raw.slice(1);
  }
  return raw;
}

// ---------------------------------------------------------------------------
// Currency detection
// ---------------------------------------------------------------------------

const CURRENCY_NAME_RE = /(^|_)usd$|(^|_)usd(_|$)/;
// A title marks the chart as USD-denominated only when it names the unit
// explicitly ("… (USD)", "$M") AND is not obviously counting things — a
// title like "Holder Distribution by Balance Bucket, USD vs Non-USD" charts
// holder counts, not dollars.
const CURRENCY_TITLE_RE = /\busd\b|\$/i;
const COUNT_TITLE_GUARD_RE =
  /holders?|addresses|counts?\b|users\b|wallets|positions|transactions|\btxs?\b/i;

interface SeriesLike {
  name?: string;
  type?: string;
  data?: unknown[];
  [key: string]: unknown;
}

function detectCurrency(
  spec: Record<string, unknown>,
  series: SeriesLike[],
  titleHint: string | undefined,
): boolean {
  if (spec._cerebro_value_unit === "usd") return true;
  if (spec._cerebro_value_unit != null) return false;
  const rawNames = series
    .map((s) => s.name ?? "")
    .filter((n) => /^[a-z][a-z0-9]*(_[a-z0-9]+)*$/.test(String(n)));
  if (rawNames.length > 0 && rawNames.every((n) => CURRENCY_NAME_RE.test(String(n)))) {
    return true;
  }
  if (
    titleHint &&
    CURRENCY_TITLE_RE.test(titleHint) &&
    !COUNT_TITLE_GUARD_RE.test(titleHint)
  ) {
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Date-axis ticks
// ---------------------------------------------------------------------------

const ISO_DATE_RE = /^\d{4}-\d{2}(-\d{2})?([ T].*)?$/;

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** Build sparse, readable tick labels for an ISO-date category axis.
 * Returns null when the data doesn't look like dates. */
export function buildDateTicks(
  data: unknown[],
): { labels: Map<number, string> } | null {
  if (!Array.isArray(data) || data.length < 3) return null;
  const values = data.map(String);
  const isoCount = values.filter((v) => ISO_DATE_RE.test(v)).length;
  if (isoCount < values.length * 0.9) return null;

  const years = values.map((v) => v.slice(0, 4));
  const months = values.map((v) => Number(v.slice(5, 7)));
  const uniqueYears = new Set(years);
  const labels = new Map<number, string>();

  if (uniqueYears.size >= 4) {
    // Multi-year span: one tick at the first sample of each year.
    let prev = "";
    values.forEach((_, i) => {
      if (years[i] !== prev) {
        labels.set(i, years[i]);
        prev = years[i];
      }
    });
    return { labels };
  }

  // Distinct months across the span (year*12+month keys)
  const monthKeys = values.map((_, i) => `${years[i]}-${String(months[i]).padStart(2, "0")}`);
  const uniqueMonths = new Set(monthKeys);

  if (uniqueMonths.size >= 5) {
    // Month-level ticks. Beyond ~18 months, quarters only.
    const quarterly = uniqueMonths.size > 18;
    const multiYear = uniqueYears.size > 1;
    let prevKey = "";
    values.forEach((_, i) => {
      if (monthKeys[i] === prevKey) return;
      prevKey = monthKeys[i];
      const m = months[i];
      if (quarterly && (m - 1) % 3 !== 0) return;
      const label = multiYear
        ? m === 1 || labels.size === 0
          ? `${MONTHS[m - 1]} ${years[i].slice(2)}`
          : MONTHS[m - 1]
        : MONTHS[m - 1];
      labels.set(i, label);
    });
    return { labels };
  }

  // Short span (daily data): ~8 evenly-spaced day ticks.
  const stride = Math.max(1, Math.ceil(values.length / 8));
  values.forEach((v, i) => {
    if (i % stride !== 0) return;
    const day = Number(v.slice(8, 10));
    const m = months[i];
    labels.set(i, day ? `${day} ${MONTHS[m - 1]}` : v);
  });
  return { labels };
}

// ---------------------------------------------------------------------------
// The pass
// ---------------------------------------------------------------------------

type AnyRecord = Record<string, unknown>;

function asArray<T>(v: T | T[] | undefined): T[] {
  if (v == null) return [];
  return Array.isArray(v) ? v : [v];
}

const CARTESIAN_TYPES = new Set(["line", "bar", "scatter"]);

export function applyReportPresentation(
  input: EChartsOption,
  titleHint?: string,
): EChartsOption {
  let spec: AnyRecord;
  try {
    spec = JSON.parse(JSON.stringify(input)) as AnyRecord;
  } catch {
    return input;
  }

  let series = asArray(spec.series as SeriesLike | SeriesLike[]);
  if (series.length === 0) return spec as EChartsOption;

  const types = new Set(series.map((s) => String(s.type ?? "")));
  const cartesian = [...types].every((t) => CARTESIAN_TYPES.has(t));
  const currency = detectCurrency(spec, series, titleHint);

  // Deterministic series order (color follows the entity): grouped series
  // arrive in SQL row order, which varies chart to chart — the same entity
  // would swap colors between figures of one report. Sort unstacked
  // multi-series charts by name so palette assignment is stable.
  if (
    cartesian &&
    series.length > 1 &&
    series.every((s) => s.stack == null && typeof s.name === "string")
  ) {
    series = [...series].sort((a, b) =>
      String(a.name).localeCompare(String(b.name), "en", {
        sensitivity: "base",
      }),
    );
    spec.series = series;
    const specLegend = spec.legend as AnyRecord | undefined;
    if (specLegend && Array.isArray(specLegend.data)) {
      specLegend.data = [...(specLegend.data as unknown[])].sort((a, b) =>
        String(a).localeCompare(String(b), "en", { sensitivity: "base" }),
      );
    }
  }

  // --- Humanize series + legend names -------------------------------------
  const renames = new Map<string, string>();
  for (const s of series) {
    if (typeof s.name === "string") {
      const pretty = humanizeName(s.name);
      if (pretty !== s.name) {
        renames.set(s.name, pretty);
        s.name = pretty;
      }
    }
  }
  const legend = spec.legend as AnyRecord | undefined;
  if (legend && Array.isArray(legend.data)) {
    legend.data = (legend.data as unknown[]).map((d) =>
      typeof d === "string" ? (renames.get(d) ?? d) : d,
    );
  }

  // --- Line polish ---------------------------------------------------------
  for (const s of series) {
    if (s.type !== "line") continue;
    const points = Array.isArray(s.data) ? s.data.length : 0;
    if (s.smooth === true) s.smooth = 0.15;
    if (s.symbolSize == null || s.symbolSize === 2) {
      if (points > 24) {
        s.showSymbol = false;
        s.symbolSize = 0;
      } else {
        s.symbolSize = 5;
      }
    }
    if (s.lineStyle == null) s.lineStyle = { width: 2 };
  }

  // --- Scatter polish ------------------------------------------------------
  const scatterSeries = series.filter((s) => s.type === "scatter");
  const labeledPointCloud =
    scatterSeries.length >= 4 &&
    scatterSeries.length === series.length &&
    scatterSeries.every((s) => Array.isArray(s.data) && s.data.length === 1);
  for (const s of scatterSeries) {
    if (s.symbolSize == null || s.symbolSize === 6) {
      s.symbolSize = labeledPointCloud ? 11 : 9;
    }
    if (labeledPointCloud) {
      s.label = {
        show: true,
        position: "right",
        distance: 7,
        fontSize: 11,
        fontFamily: "JetBrains Mono, ui-monospace, Menlo, monospace",
        formatter: () => String(s.name ?? ""),
        ...(typeof s.label === "object" ? (s.label as AnyRecord) : {}),
      };
      // Cluster-safe labels: drop overlapping ones instead of colliding.
      if (s.labelLayout == null) s.labelLayout = { hideOverlap: true };
    }
  }
  if (labeledPointCloud && legend && legend.show == null) {
    legend.show = false;
  }

  // --- Single-series cartesian: the card title names the series ------------
  if (
    cartesian &&
    series.length === 1 &&
    legend &&
    legend.show == null &&
    !labeledPointCloud
  ) {
    legend.show = false;
  }

  // --- Legend placement -----------------------------------------------------
  const legendShown =
    legend != null &&
    legend.show !== false &&
    (series.length > 1 || !cartesian);
  if (legend && legendShown && cartesian && legend.left == null && legend.orient == null) {
    legend.left = 0;
  }

  // --- Axes ----------------------------------------------------------------
  const xAxes = asArray(spec.xAxis as AnyRecord | AnyRecord[]);
  const yAxes = asArray(spec.yAxis as AnyRecord | AnyRecord[]);

  // Labeled point clouds spanning orders of magnitude read as an L hugging
  // the axes on linear scales — switch to log so the cluster spreads out.
  if (labeledPointCloud && xAxes.length === 1 && yAxes.length === 1) {
    const points = scatterSeries
      .flatMap((s) => (Array.isArray(s.data) ? s.data : []))
      .filter((p): p is [number, number] => Array.isArray(p) && p.length >= 2);
    const maybeLog = (axis: AnyRecord, values: number[]) => {
      if (axis.type !== "value") return;
      const nums = values.filter((v) => typeof v === "number");
      if (nums.length === 0) return;
      const min = Math.min(...nums);
      const max = Math.max(...nums);
      if (min > 0 && max / min > 50) {
        axis.type = "log";
        axis.splitNumber = 4;
      }
    };
    maybeLog(xAxes[0], points.map((p) => Number(p[0])));
    maybeLog(yAxes[0], points.map((p) => Number(p[1])));
  }

  const applyValueFormatter = (axis: AnyRecord) => {
    if (axis.type !== "value" && axis.type !== "log") return;
    const axisLabel = (axis.axisLabel ??= {}) as AnyRecord;
    if (axisLabel.formatter == null) {
      const axisCurrency =
        currency || CURRENCY_NAME_RE.test(String(axis.name ?? "").toLowerCase());
      axisLabel.formatter = (v: number) => formatCompact(v, axisCurrency);
    }
    if (typeof axis.name === "string" && axis.name) {
      axis.name = humanizeName(axis.name);
      if (axis.nameLocation == null) {
        axis.nameLocation = "middle";
        axis.nameGap = yAxes.includes(axis) ? 48 : 30;
      }
    }
  };

  for (const axis of [...xAxes, ...yAxes]) {
    applyValueFormatter(axis);
    if (axis.type === "category" && Array.isArray(axis.data)) {
      const axisLabel = (axis.axisLabel ??= {}) as AnyRecord;
      const ticks = buildDateTicks(axis.data as unknown[]);
      if (ticks && axisLabel.formatter == null && axisLabel.interval == null) {
        const labels = ticks.labels;
        axisLabel.interval = (index: number) => labels.has(index);
        axisLabel.formatter = (_value: string, index: number) =>
          labels.get(index) ?? "";
        axisLabel.hideOverlap = true;
      } else if (
        !ticks &&
        axisLabel.interval == null &&
        (axis.data as unknown[]).length <= 12
      ) {
        // few categories (buckets, tokens): show every label
        axisLabel.interval = 0;
        // Strip "1 " / "2. " ordering prefixes that SQL adds for sort order
        // ("1 dust ≤$1" -> "dust ≤$1") when every category carries one.
        const cats = (axis.data as unknown[]).map(String);
        const PREFIX = /^\d+[\s._-]+(?=\S)/;
        if (
          cats.length >= 3 &&
          cats.every((c) => PREFIX.test(c)) &&
          axisLabel.formatter == null
        ) {
          axisLabel.formatter = (value: string) => value.replace(PREFIX, "");
        }
      }
    }
  }

  // --- Grid ----------------------------------------------------------------
  if (cartesian && spec.grid != null && !Array.isArray(spec.grid)) {
    const grid = spec.grid as AnyRecord;
    // Builder defaults: {left:"3%", right:"4%"/"6%", bottom:"10%", top:"40"}.
    // Tighten only when the spec still carries those defaults.
    if (grid.top === "40" || grid.top === 40) {
      grid.top = legendShown ? 42 : 20;
    }
    if (grid.bottom === "10%") {
      grid.bottom = xAxes.some((a) => a.name) ? 34 : 8;
    }
    if (grid.left === "3%") grid.left = yAxes.some((a) => a.name) ? 34 : 8;
    if (grid.right === "4%" || grid.right === "6%") {
      // Labeled point clouds draw series names to the right of each dot —
      // reserve room so edge labels don't clip.
      grid.right = labeledPointCloud ? 64 : 18;
    }
    grid.containLabel = true;
  }

  // --- Tooltip -------------------------------------------------------------
  const tooltip = (spec.tooltip ??= {}) as AnyRecord;
  if (tooltip.formatter == null && tooltip.valueFormatter == null) {
    tooltip.valueFormatter = (v: unknown) =>
      typeof v === "number" ? formatFull(v, currency) : String(v ?? "—");
  }

  return spec as EChartsOption;
}
