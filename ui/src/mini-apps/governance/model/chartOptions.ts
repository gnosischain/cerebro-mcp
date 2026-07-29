// ECharts option builders for the Governance Explorer. Convention (frozen
// across all cerebro chart surfaces): dataZoom is INSIDE-only — wheel/pinch,
// never a slider bar. Theming comes from ChartCard (cerebro-dark/light).

import type { EChartsOption } from "echarts";
import type { ActivityRow, ConcentrationRow } from "../types";
import { finite } from "../../shared/rowDataset";
import { sanitizeSymbol } from "../../shared/TokenIdentity";
import { shortAddr } from "../../../utils/format";
import {
  LABEL_FONT,
  MAX_SERIES_DEFAULT,
  OTHER_LABEL,
  RESIDUAL_COLOR,
  escapeHtml,
  fmtUsdCompact,
  insideZoom,
  stackedSeriesOption,
  treemapOption,
} from "../../shared/chartOptions";

// insideZoom and LABEL_FONT moved to shared/chartOptions when the Treasury tab
// needed the same builders; re-exported here so existing importers are unchanged.
export { insideZoom };

export interface ActivitySeriesDef {
  field: string;
  label: string;
  type: "bar" | "line";
  /** 1 = plot on a second value axis (dominant counts like votes). */
  yAxisIndex?: 0 | 1;
}

/** Time-bucketed activity combo (bars + lines). The bucket unit comes from
 * the rows' `bucket_unit` constant column, not from view state. */
export function activityComboOption(
  rows: ActivityRow[],
  series: ActivitySeriesDef[],
  secondAxisName = "",
): EChartsOption {
  const buckets = rows.map((row) => row.bucket);
  const unit = rows[0]?.bucket_unit ?? "day";
  const hasSecond = series.some((def) => def.yAxisIndex === 1);
  return {
    tooltip: { trigger: "axis" },
    legend: { show: series.length > 1 },
    grid: { left: 60, right: hasSecond ? 64 : 24, top: 42, bottom: 48 },
    xAxis: {
      type: "category",
      data: buckets,
      name: `per ${unit}`,
      nameTextStyle: { fontFamily: LABEL_FONT, fontSize: 10 },
    },
    yAxis: hasSecond
      ? [{ type: "value" }, { type: "value", name: secondAxisName, splitLine: { show: false } }]
      : { type: "value" },
    dataZoom: insideZoom,
    series: series.map((def) => ({
      name: def.label,
      type: def.type,
      yAxisIndex: def.yAxisIndex ?? 0,
      ...(def.type === "bar" ? { barMaxWidth: 22 } : { showSymbol: false, smooth: true }),
      data: rows.map((row) => Number(row[def.field] ?? 0)),
    })),
  } as EChartsOption;
}

/** Category share donut (proposal types, …). */
export function donutOption(pairs: Array<{ name: string; value: number }>): EChartsOption {
  return {
    tooltip: { trigger: "item" },
    legend: { bottom: 0, textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    series: [{
      type: "pie",
      radius: ["44%", "70%"],
      center: ["50%", "44%"],
      label: { fontFamily: LABEL_FONT, fontSize: 11 },
      data: pairs,
    }],
  } as EChartsOption;
}

/** Vertical bars over a categorical axis (quorum attainment, …). */
export function categoryCountOption(
  rows: Array<{ name: string; value: number }>,
  valueLabel: string,
): EChartsOption {
  return {
    tooltip: { trigger: "axis" },
    grid: { left: 58, right: 24, top: 32, bottom: 42 },
    xAxis: {
      type: "category",
      data: rows.map((row) => row.name),
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 11 },
    },
    yAxis: { type: "value" },
    series: [{ name: valueLabel, type: "bar", barMaxWidth: 44, data: rows.map((row) => row.value) }],
  } as EChartsOption;
}

/** Horizontal bars (forum category activity — long category names). */
export function horizontalBarOption(
  rows: Array<{ name: string; value: number }>,
  valueLabel: string,
): EChartsOption {
  const sorted = [...rows].sort((a, b) => a.value - b.value);
  const height = Math.max(300, Math.min(680, 90 + sorted.length * 26));
  return {
    _cerebro_height: `${height}px`,
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { left: 170, right: 40, top: 20, bottom: 34 },
    xAxis: { type: "value" },
    yAxis: {
      type: "category",
      data: sorted.map((row) => row.name),
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 10, width: 150, overflow: "truncate" },
    },
    series: [{ name: valueLabel, type: "bar", barMaxWidth: 16, data: sorted.map((row) => row.value) }],
  } as EChartsOption;
}

/** Top-N voting-power / vote-count concentration tiers as percent bars. */
export function concentrationOption(
  rows: Array<Pick<ConcentrationRow, "tier" | "share">>,
  shareLabel: string,
): EChartsOption {
  const sorted = [...rows].sort((a, b) => a.tier - b.tier);
  return {
    tooltip: {
      trigger: "axis",
      valueFormatter: (value: unknown) => `${(Number(value) * 100).toFixed(1)}%`,
    },
    grid: { left: 58, right: 24, top: 32, bottom: 42 },
    xAxis: {
      type: "category",
      data: sorted.map((row) => `Top ${row.tier}`),
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 11 },
    },
    yAxis: {
      type: "value",
      max: 1,
      axisLabel: { formatter: (value: number) => `${Math.round(value * 100)}%` },
    },
    series: [{
      name: shareLabel,
      type: "bar",
      barMaxWidth: 44,
      data: sorted.map((row) => row.share ?? 0),
      label: {
        show: true,
        position: "top",
        fontFamily: LABEL_FONT,
        fontSize: 10,
        formatter: (params: unknown) =>
          `${(Number((params as { value?: number }).value ?? 0) * 100).toFixed(1)}%`,
      },
    }],
  } as EChartsOption;
}

// ---------------------------------------------------------------------------
// Treasury builders
//
// The treasury history plane stores UNITS at monthly anchors and carries no
// price series, so every builder below is explicit about which axis is a unit
// and which is a currency. The only currency axis here is the constant-price
// revaluation, and it says so on the axis itself.
//
// Series shapes are declared STRUCTURALLY and locally rather than imported from
// model/treasuryHistory: the two modules stay decoupled, and structural typing
// makes the real types assignable.
// ---------------------------------------------------------------------------

/** Fixed amber for the Ltd. entity. Fixed, not palette-assigned, because the
 * DAO/Ltd split is the point of the chart — if the hue moved with legend order
 * the split would only be readable by reading the legend. */
export const LTD_SERIES_COLOR = "#F5B14C";

/** Lead band of the concentration bar. */
const LEAD_SERIES_COLOR = "#B4F03C";

/** The wallet history folds its small-wallet tail server-side under this
 * literal wallet_address. */
const RESIDUAL_WALLET = "other";

/** A currency-named axis on the units plane would assert a market value nobody
 * measured — there are no historical prices here, only monthly unit anchors.
 * The input is a code constant, so this fires on the first render in dev and
 * can never fire on user data. */
function assertUnitAxis(unitLabel: string): string {
  if (/\$|(^|[^a-z])(usd|dollars?|eur)([^a-z]|$)/i.test(unitLabel)) {
    throw new Error(
      `timeSeriesLineOption: unitLabel "${unitLabel}" names a currency, but this plane has `
      + "no historical prices. Use constantPriceStackOption for a constant-price revaluation.",
    );
  }
  return unitLabel;
}

/** Token symbols are attacker-authored: sanitize (controls, bidi overrides,
 * length) before anything reaches a legend, axis name or tooltip. The address
 * is the identity, so an unnamed token falls back to it rather than to a
 * placeholder that could be confused with a real ticker. */
function tokenLabel(entry: { token: string; label?: string }): string {
  return sanitizeSymbol(entry.label) || shortAddr(entry.token);
}

/** 19 held tokens claim the symbol "USDC" and 2 claim "SAFE". When a label is
 * not unique inside one chart the address is appended, mirroring what
 * TokenIdentity does with its `ambiguous` prop. */
function disambiguate(labels: Map<string, string>): Map<string, string> {
  const counts = new Map<string, number>();
  for (const label of labels.values()) counts.set(label, (counts.get(label) ?? 0) + 1);
  const out = new Map<string, string>();
  for (const [key, label] of labels) {
    out.set(key, (counts.get(label) ?? 0) > 1 ? `${label} ${shortAddr(key)}` : label);
  }
  return out;
}

/** Buckets arrive grouped by series, so a token first held late in the history
 * would otherwise append its buckets after the earlier ones. Bucket keys are
 * YYYY-MM-01, where lexicographic order is chronological order. */
function byBucket(rows: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  return [...rows].sort((a, b) => String(a.bucket).localeCompare(String(b.bucket)));
}

export interface TimeLineSeriesDef {
  field: string;
  label: string;
  color?: string;
  dashed?: boolean;
}

/** ONE chain's snapshot series over time. `unitLabel` becomes the y-axis name
 * and must name a unit ("GNO"), never a currency.
 *
 * Never pass two chains through one call: chain 1 has 69 monthly buckets and
 * chain 100 has 30 that stop in 2022-11, so a shared category axis would imply
 * a contemporaneity that does not exist. */
export function timeSeriesLineOption(
  rows: Array<Record<string, unknown>>,
  args: { xField: string; series: TimeLineSeriesDef[]; unitLabel: string; yScale?: "value" | "log" },
): EChartsOption {
  const unitLabel = assertUnitAxis(args.unitLabel);
  const buckets = rows.map((row) => String(row[args.xField] ?? ""));
  const series: Array<Record<string, unknown>> = args.series.map((def) => {
    const lineStyle: Record<string, unknown> = {};
    if (def.color) lineStyle.color = def.color;
    if (def.dashed) lineStyle.type = "dashed";
    return {
      name: def.label,
      type: "line",
      showSymbol: false,
      // finite() keeps a missing snapshot NULL so ECharts draws a gap. Number()
      // would turn it into a plunge to zero — a holding that was never measured
      // is not a holding that went to nothing.
      data: rows.map((row) => finite(row[def.field])),
      connectNulls: false,
      ...(Object.keys(lineStyle).length ? { lineStyle } : {}),
      ...(def.color ? { itemStyle: { color: def.color } } : {}),
    };
  });
  return {
    tooltip: { trigger: "axis" },
    legend: { show: args.series.length > 1, textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    grid: { left: 70, right: 24, top: 42, bottom: 48 },
    xAxis: {
      type: "category",
      data: buckets,
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 10, hideOverlap: true },
    },
    yAxis: {
      // A log axis silently drops non-positive points; callers pick it only for
      // series that are strictly positive by construction (unit balances).
      type: args.yScale ?? "value",
      name: unitLabel,
      nameTextStyle: { fontFamily: LABEL_FONT, fontSize: 10 },
      scale: true,
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 10 },
    },
    dataZoom: insideZoom,
    series,
  } as EChartsOption;
}

/** Metadata breadth over time: named vs unnamed token COUNTS stacked (both are
 * counts of the same thing, which is what makes the stack legitimate) with the
 * position count as a line on a second axis. */
export function breadthOption(
  rows: Array<Record<string, unknown>>,
  args: { xField: string; namedField: string; heldField: string; positionsField?: string },
): EChartsOption {
  const buckets = rows.map((row) => String(row[args.xField] ?? ""));
  const named = rows.map((row) => finite(row[args.namedField]));
  const held = rows.map((row) => finite(row[args.heldField]));
  // Unnamed is DERIVED (held - named). If either side is missing the difference
  // is unknowable, so the bucket stays null instead of claiming the whole bar.
  const unnamed = held.map((total, index) => {
    const isNamed = named[index];
    if (total === null || isNamed === null) return null;
    return Math.max(0, total - isNamed);
  });
  const series: Array<Record<string, unknown>> = [
    { name: "Named", type: "bar", stack: "tokens", barMaxWidth: 22, data: named },
    {
      name: "Unnamed",
      type: "bar",
      stack: "tokens",
      barMaxWidth: 22,
      // Explicit grey rather than the next palette hue: an unresolved token is
      // an absence of identity, and TokenIdentity applies the same rule to its
      // monogram fallback.
      itemStyle: { color: RESIDUAL_COLOR },
      data: unnamed,
    },
  ];
  if (args.positionsField) {
    series.push({
      name: "Positions",
      type: "line",
      yAxisIndex: 1,
      showSymbol: false,
      data: rows.map((row) => finite(row[args.positionsField!])),
      connectNulls: false,
    });
  }
  return {
    tooltip: { trigger: "axis" },
    legend: { textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    grid: { left: 62, right: args.positionsField ? 66 : 24, top: 42, bottom: 48 },
    xAxis: {
      type: "category",
      data: buckets,
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 10, hideOverlap: true },
    },
    yAxis: [
      {
        type: "value",
        name: "tokens",
        nameTextStyle: { fontFamily: LABEL_FONT, fontSize: 10 },
        axisLabel: { fontFamily: LABEL_FONT, fontSize: 10 },
      },
      {
        type: "value",
        name: "positions",
        nameTextStyle: { fontFamily: LABEL_FONT, fontSize: 10 },
        axisLabel: { fontFamily: LABEL_FONT, fontSize: 10 },
        splitLine: { show: false },
      },
    ],
    dataZoom: insideZoom,
    series,
  } as EChartsOption;
}

/** One token's units through time. Structural mirror of the treasuryHistory
 * series shape — declared, never imported, so the modules stay decoupled. */
export interface TokenUnitsSeries {
  /** Lowercase token address. THE identity; `label` is untrusted display text. */
  token: string;
  label?: string;
  /** Current spot price in USD. May instead be supplied through `args.prices`,
   * which is the shape `state.price_overlay.by_chain[chainId]` already has. */
  price?: number | null;
  points: Array<{ bucket: string; units: number | null }>;
}

/** Stacked area of units x CURRENT spot price. This is a CONSTANT-PRICE
 * revaluation of a historical unit series, NOT historical market value — the
 * caller must caption it as such; the axis says so too.
 *
 * Tokens with no price are EXCLUDED and returned in `_cerebro_excluded`: a
 * missing price is not a zero price, and valuing an unpriced holding at zero
 * would quietly shrink NAV. That exclusion doubles as a safety signal — every
 * one of the 19 tokens claiming the symbol "USDC" is unpriced. */
export function constantPriceStackOption(
  series: TokenUnitsSeries[],
  args: { maxSeries?: number; prices?: Record<string, number | null> } = {},
): EChartsOption & { _cerebro_excluded: string[] } {
  const excluded: string[] = [];
  const rawLabels = new Map<string, string>();
  const rows: Array<Record<string, unknown>> = [];
  for (const entry of series) {
    const token = entry.token.toLowerCase();
    const price = args.prices?.[token] ?? entry.price ?? null;
    // A non-positive price is not a price either.
    if (price === null || !Number.isFinite(price) || price <= 0) {
      excluded.push(tokenLabel(entry));
      continue;
    }
    rawLabels.set(token, tokenLabel(entry));
    for (const point of entry.points) {
      const units = finite(point.units);
      if (units === null) continue;
      rows.push({ bucket: point.bucket, token, usd: units * price });
    }
  }
  const labels = disambiguate(rawLabels);
  const spec = stackedSeriesOption(byBucket(rows), {
    xField: "bucket",
    valueField: "usd",
    // Keyed on the ADDRESS: keying on the symbol would merge every "USDC"
    // claimant into one band.
    seriesField: "token",
    kind: "area",
    maxSeries: args.maxSeries ?? MAX_SERIES_DEFAULT,
    seriesLabeler: (token) => labels.get(token) ?? shortAddr(token),
    yName: "USD at constant price",
    valueFormatter: fmtUsdCompact,
  });
  return { ...spec, _cerebro_excluded: excluded } as EChartsOption & { _cerebro_excluded: string[] };
}

/** One wallet's units of a single token. Structural mirror, as above. */
export interface WalletUnitsSeries {
  /** Lowercase wallet address, or the literal "other" for the server-folded tail. */
  wallet: string;
  label?: string;
  isLtd?: boolean;
  points: Array<{ bucket: string; units: number | null }>;
}

/** Per-wallet stack of ONE token's units. Every band is the same unit, so the
 * stack total is meaningful. Ltd. carries a fixed amber hue and an "(Ltd.)"
 * suffix so the DAO/Ltd split reads without consulting the legend order; the
 * residual tail always plots last and stays muted. */
export function walletStackOption(
  series: WalletUnitsSeries[],
  args: { unitLabel: string; maxSeries?: number },
): EChartsOption {
  const labels: Record<string, string> = {};
  const colors: Record<string, string> = {};
  const rows: Array<Record<string, unknown>> = [];
  for (const entry of series) {
    const wallet = entry.wallet.toLowerCase();
    if (wallet === RESIDUAL_WALLET) {
      labels[wallet] = OTHER_LABEL;
    } else {
      // A caller-supplied nickname is untrusted display text too.
      const base = sanitizeSymbol(entry.label) || shortAddr(entry.wallet);
      labels[wallet] = entry.isLtd ? `${base} (Ltd.)` : base;
      if (entry.isLtd) colors[wallet] = LTD_SERIES_COLOR;
    }
    for (const point of entry.points) {
      const units = finite(point.units);
      if (units === null) continue;
      rows.push({ bucket: point.bucket, wallet, units });
    }
  }
  return stackedSeriesOption(byBucket(rows), {
    xField: "bucket",
    valueField: "units",
    seriesField: "wallet",
    kind: "area",
    maxSeries: args.maxSeries ?? MAX_SERIES_DEFAULT,
    residualKey: RESIDUAL_WALLET,
    seriesColors: colors,
    seriesLabeler: (wallet) => labels[wallet] ?? shortAddr(wallet),
    // A token symbol, not a currency: this is a unit stack.
    yName: sanitizeSymbol(args.unitLabel) || "units",
  });
}

/** USD composition treemap for ONE chain. Nodes carry the token address as
 * their id so a click can drill down on identity rather than on the symbol.
 *
 * Holdings without a positive USD value are not drawable in a treemap; they are
 * returned in `_cerebro_dropped` rather than disappearing. */
export function compositionTreemapOption(
  items: Array<{ token: string; label: string; usd: number }>,
): EChartsOption & { _cerebro_dropped: string[] } {
  const dropped: string[] = [];
  const rawLabels = new Map<string, string>();
  const usable: Array<{ token: string; usd: number }> = [];
  for (const item of items) {
    const token = item.token.toLowerCase();
    const usd = finite(item.usd);
    if (usd === null || usd <= 0) {
      dropped.push(tokenLabel(item));
      continue;
    }
    rawLabels.set(token, tokenLabel(item));
    usable.push({ token, usd });
  }
  const labels = disambiguate(rawLabels);
  const spec = treemapOption(
    usable.map((item) => ({
      id: item.token,
      name: labels.get(item.token) ?? shortAddr(item.token),
      value: item.usd,
    })),
    { valueFormatter: fmtUsdCompact, height: "380px", clickable: true },
  );
  return { ...spec, _cerebro_dropped: dropped } as EChartsOption & { _cerebro_dropped: string[] };
}

/** Horizontal split bar: one dominant holding against everything else. This is
 * the concentration headline — GNO is 80.5% of priced NAV, and a treemap cannot
 * make a single ratio that large legible where one stacked bar can. */
export function concentrationBarOption(
  args: { leadLabel: string; leadUsd: number; restUsd: number },
): EChartsOption {
  const lead = Math.max(0, finite(args.leadUsd) ?? 0);
  const rest = Math.max(0, finite(args.restUsd) ?? 0);
  const total = lead + rest;
  const leadLabel = sanitizeSymbol(args.leadLabel) || "Lead holding";
  const restLabel = "Everything else";
  // With no priced NAV there is no share to state. A dash, never "0.0%", which
  // would assert a measured zero.
  const share = (value: number) => (total > 0 ? `${((value / total) * 100).toFixed(1)}%` : "—");
  const band = (name: string, value: number, color: string, position: "insideLeft" | "insideRight") => ({
    name,
    type: "bar",
    stack: "nav",
    barWidth: 30,
    itemStyle: { color },
    data: [value],
    label: {
      // A band under ~12% cannot hold its label without spilling onto its
      // neighbour; the legend and tooltip still carry it.
      show: total > 0 && value / total >= 0.12,
      position,
      fontFamily: LABEL_FONT,
      fontSize: 11,
      // A function, not a template string: a "{b}"-shaped token smuggled into
      // an attacker-authored symbol would otherwise be interpolated.
      formatter: () => `${name} ${share(value)}`,
    },
  });
  const series: Array<Record<string, unknown>> = [
    band(leadLabel, lead, LEAD_SERIES_COLOR, "insideLeft"),
    band(restLabel, rest, RESIDUAL_COLOR, "insideRight"),
  ];
  return {
    _cerebro_height: "150px",
    tooltip: {
      trigger: "item",
      formatter: (params: unknown) => {
        const item = params as { seriesName?: string; value?: number };
        const value = Number(item.value ?? 0);
        return `${escapeHtml(item.seriesName)}: ${escapeHtml(fmtUsdCompact(value))} (${escapeHtml(share(value))})`;
      },
    },
    legend: { top: 0, textStyle: { fontFamily: LABEL_FONT, fontSize: 11 } },
    grid: { left: 12, right: 12, top: 46, bottom: 12 },
    // Both axes are hidden: the bar IS the scale, and a tick axis under a
    // single 100%-wide bar reads as a second, contradictory measurement.
    xAxis: { type: "value", max: total > 0 ? total : 1, show: false },
    yAxis: { type: "category", data: [""], show: false },
    series,
  } as EChartsOption;
}
