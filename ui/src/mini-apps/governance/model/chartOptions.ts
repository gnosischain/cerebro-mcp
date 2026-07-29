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
    _cerebro_height: "620px",
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

// ---------------------------------------------------------------------------
// GIP knowledge graph
// ---------------------------------------------------------------------------
//
// The structure of this graph, measured rather than assumed, is what picks the
// layout:
//
//   * 90.4% of citations point BACKWARD in GIP number (141 of 156) — a newer
//     GIP cites an older one. It is very nearly a temporal DAG.
//   * 57 of 149 nodes have no edge at all.
//   * Max degree is 13 and 24 nodes have degree 1 — sparse, with no real
//     community structure.
//
// A force layout throws all three away: it hides the chronology that IS the
// signal, scatters the 57 isolates as noise, and finds clusters in a graph that
// does not have any. So the default is a TIMELINE, carrying four dimensions:
//
//   x  when       — the GIP's first-seen DATE. Not its number: GIP numbers run
//                   only 89% in date order (17 inversions across 148 pairs), so
//                   the number is a label and the date is the chronology.
//   y  influence  — citations RECEIVED. Lifecycle stage was tried here first
//                   and failed: 121 of 149 GIPs are 'voted', so the lanes
//                   collapsed into one line and every arc went flat.
//   colour stage  — the lifecycle, which needs a channel but not an axis.
//   size   volume — forum posts, i.e. how much was said, not how much it
//                   mattered. Those are different claims and get different
//                   channels.
//
// The force view stays available for the rare "who clumps with whom" question.

export interface GipNode {
  gip: number;
  label: string;
  stage: string;
  posts: number | null;
  participants: number | null;
  views: number | null;
  votes: number | null;
  quorumStatus: string;
  author: string;
  proposalState: string;
  firstSeen: string;
  lastActivity: string;
  topicId: number | null;
  proposalId: string;
}

export interface GipEdge {
  src: number;
  dst: number;
  weight: number;
  topics: number | null;
  firstMention: string;
  lastMention: string;
}

/** Lifecycle stage -> colour AND lane. Fixed, not palette-assigned: the stages
 * are an ordered lifecycle, so the reader learns the mapping once and carries
 * it between renders and between the two layouts. */
const STAGE_COLORS: Record<string, string> = {
  voted: "#a5e05a",
  "phase-3": "#7c9cf5",
  "phase-2": "#b58cf0",
  "phase-1": "#6f7a8c",
  unstaged: "#4a5160",
};

export const GIP_STAGE_ORDER = ["voted", "phase-3", "phase-2", "phase-1", "unstaged"];

export const GIP_STAGE_COLOR = (stage: string): string =>
  STAGE_COLORS[stage] ?? STAGE_COLORS.unstaged;

export interface GipDegree { inbound: number; outbound: number; weight: number }

/** In/out citation degree per GIP. Derived here rather than in SQL because the
 * client already holds every edge, and computing it server-side would mean a
 * second full scan of the post bodies. */
export function gipDegrees(edges: GipEdge[]): Map<number, GipDegree> {
  const out = new Map<number, GipDegree>();
  const bump = (gip: number, key: "inbound" | "outbound", weight: number) => {
    const entry = out.get(gip) ?? { inbound: 0, outbound: 0, weight: 0 };
    entry[key] += 1;
    entry.weight += weight;
    out.set(gip, entry);
  };
  for (const e of edges) {
    bump(e.src, "outbound", e.weight);
    bump(e.dst, "inbound", e.weight);
  }
  return out;
}

/** Edges whose BOTH endpoints exist as nodes.
 *
 * Load-bearing: ECharts silently invents a node for an unknown link endpoint,
 * which renders as a real GIP that merely has no data. GIP numbers appear in
 * post bodies far more often than they exist as a topic or proposal, so this is
 * the common case, not an edge case. */
export function drawableEdges(nodes: GipNode[], edges: GipEdge[]): GipEdge[] {
  const present = new Set(nodes.map((n) => n.gip));
  return edges.filter((e) => present.has(e.src) && present.has(e.dst));
}

/** Size = forum posts, i.e. how much was SAID. Influence has its own axis, so
 * conflating the two here would spend two channels on one measure. Clamped so a
 * 1-post stub is still clickable and a 300-post megathread cannot eat the plot. */
function nodeSize(posts: number | null, maxPosts: number): number {
  return 7 + Math.sqrt(Math.max(0, posts ?? 0) / Math.max(1, maxPosts)) * 20;
}

function tooltipHtml(d: Record<string, unknown>, deg: GipDegree | undefined): string {
  const rows = [
    `<strong>GIP-${d.gip}</strong>`,
    escapeHtml(String(d.fullLabel ?? "")),
    `<span style="opacity:.7">${escapeHtml(String(d.stage ?? ""))}` +
      `${d.proposalState ? ` · ${escapeHtml(String(d.proposalState))}` : ""}` +
      `${d.quorumStatus ? ` · quorum ${escapeHtml(String(d.quorumStatus))}` : ""}</span>`,
    `cited by ${deg?.inbound ?? 0} · cites ${deg?.outbound ?? 0}`,
    `${d.posts ?? 0} posts · ${d.participants ?? 0} participants${
      Number(d.votes) > 0 ? ` · ${d.votes} votes` : ""
    }`,
    `first seen ${String(d.firstSeen ?? "").slice(0, 10)}`,
  ];
  return rows.filter(Boolean).join("<br/>");
}

interface GraphOpts {
  focus?: number | null;
  /** Hide the 57 nodes with no citation in either direction. */
  hideIsolated?: boolean;
  stages?: string[];
}

function visibleNodes(nodes: GipNode[], degrees: Map<number, GipDegree>, opts?: GraphOpts): GipNode[] {
  const stages = opts?.stages;
  return nodes.filter((n) => {
    if (stages && stages.length > 0 && !stages.includes(n.stage)) return false;
    if (opts?.hideIsolated && !degrees.has(n.gip)) return false;
    return true;
  });
}

/** TIMELINE (default). x = real first-seen date, y = lifecycle lane, links = arcs.
 *
 * Arc direction encodes something real: a backward arc (right to left) is the
 * normal case — a newer GIP citing an older one. The 15 forward arcs are drawn
 * on the opposite side so they stand out, because a GIP citing a LATER one only
 * happens when a thread was edited after the fact. */
export function gipTimelineOption(
  nodes: GipNode[],
  edges: GipEdge[],
  opts?: GraphOpts,
): EChartsOption {
  const degrees = gipDegrees(drawableEdges(nodes, edges));
  const shown = visibleNodes(nodes, degrees, opts);
  const present = new Set(shown.map((n) => n.gip));
  const links = drawableEdges(nodes, edges).filter((e) => present.has(e.src) && present.has(e.dst));
  const focus = opts?.focus ?? null;
  const maxPosts = Math.max(1, ...shown.map((n) => n.posts ?? 0));

  return {
    tooltip: {
      confine: true,
      textStyle: { fontFamily: LABEL_FONT, fontSize: 11 },
      formatter: (p: unknown) => {
        const param = p as { dataType?: string; data?: Record<string, unknown> };
        const d = param.data ?? {};
        if (param.dataType === "edge") {
          return [
            `GIP-${d.srcGip} <span style="opacity:.6">cites</span> GIP-${d.dstGip}`,
            `${d.weight} mention${Number(d.weight) === 1 ? "" : "s"}`,
            `${String(d.firstMention ?? "").slice(0, 10)} \u2192 ${String(d.lastMention ?? "").slice(0, 10)}`,
          ].join("<br/>");
        }
        return tooltipHtml(d, degrees.get(Number(d.gip)));
      },
    },
    grid: { left: 62, right: 28, top: 28, bottom: 44 },
    xAxis: {
      type: "time",
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 10 },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      name: "citations received",
      nameTextStyle: { fontFamily: LABEL_FONT, fontSize: 10 },
      nameLocation: "middle",
      nameGap: 40,
      minInterval: 1,
      axisLabel: { fontFamily: LABEL_FONT, fontSize: 10 },
      splitLine: { show: true, lineStyle: { opacity: 0.1 } },
    },
    // Wheel must scroll the PAGE, not the chart. A 560px canvas that swallows
    // every wheel event traps the reader: they scroll, nothing moves, and the
    // chart silently zooms instead. Zoom is still available on ctrl/meta+wheel
    // and on drag, which is the standard map gesture and is discoverable from
    // the caption.
    dataZoom: [{
      type: "inside",
      zoomOnMouseWheel: "ctrl",
      moveOnMouseWheel: false,
      moveOnMouseMove: true,
    }],
    series: [{
      type: "graph",
      coordinateSystem: "cartesian2d",
      // The axes ARE the layout — nothing to simulate.
      layout: "none",
      emphasis: { focus: "adjacency", label: { show: true } },
      label: {
        show: true,
        position: "top",
        formatter: (p: unknown) => {
          const d = (p as { data?: Record<string, unknown> }).data ?? {};
          const inbound = degrees.get(Number(d.gip))?.inbound ?? 0;
          // Only label what a reader can act on: the hubs and the pinned node.
          return inbound >= 4 || Number(d.gip) === focus ? `GIP-${d.gip}` : "";
        },
        fontFamily: LABEL_FONT,
        fontSize: 9,
      },
      data: shown.map((n) => ({
        name: `GIP-${n.gip}`,
        value: [n.firstSeen.replace(" ", "T"), degrees.get(n.gip)?.inbound ?? 0],
        gip: n.gip,
        fullLabel: n.label,
        stage: n.stage,
        proposalState: n.proposalState,
        quorumStatus: n.quorumStatus,
        posts: n.posts,
        participants: n.participants,
        votes: n.votes,
        firstSeen: n.firstSeen,
        topicId: n.topicId,
        proposalId: n.proposalId,
        symbolSize: nodeSize(n.posts, maxPosts),
        itemStyle: {
          color: GIP_STAGE_COLOR(n.stage),
          borderColor: n.gip === focus ? "#fff" : "transparent",
          borderWidth: n.gip === focus ? 2 : 0,
          opacity: focus === null || n.gip === focus ? 0.95 : 0.5,
        },
      })),
      links: links.map((e) => {
        const backward = e.dst < e.src;
        const touchesFocus = focus !== null && (e.src === focus || e.dst === focus);
        return {
          source: `GIP-${e.src}`,
          target: `GIP-${e.dst}`,
          srcGip: e.src,
          dstGip: e.dst,
          weight: e.weight,
          firstMention: e.firstMention,
          lastMention: e.lastMention,
          lineStyle: {
            // Sign flips the arc to the other side, so the 15 forward citations
            // are visibly different from the 141 backward ones rather than
            // blending in. 0.4 rather than a gentle bow: most citations land
            // between nodes at similar heights, and a flat line reads as noise.
            curveness: backward ? 0.4 : -0.4,
            width: Math.min(5, 0.5 + Math.log2(e.weight + 1)),
            opacity: focus === null ? 0.34 : touchesFocus ? 0.9 : 0.05,
            color: backward ? undefined : "#e0885a",
          },
        };
      }),
    }],
    _cerebro_height: "560px",
  } as EChartsOption;
}

/** FORCE / clusters. The alternate view — no chronology, but it answers "what
 * clumps together", which the timeline cannot show. */
export function gipGraphOption(
  nodes: GipNode[],
  edges: GipEdge[],
  opts?: GraphOpts,
): EChartsOption {
  const degrees = gipDegrees(drawableEdges(nodes, edges));
  const shown = visibleNodes(nodes, degrees, opts);
  const present = new Set(shown.map((n) => n.gip));
  const links = drawableEdges(nodes, edges).filter((e) => present.has(e.src) && present.has(e.dst));
  const focus = opts?.focus ?? null;

  return {
    tooltip: {
      confine: true,
      textStyle: { fontFamily: LABEL_FONT, fontSize: 11 },
      formatter: (p: unknown) => {
        const param = p as { dataType?: string; data?: Record<string, unknown> };
        const d = param.data ?? {};
        if (param.dataType === "edge") {
          return `GIP-${d.srcGip} cites GIP-${d.dstGip}<br/>${d.weight} mention${
            Number(d.weight) === 1 ? "" : "s"
          }`;
        }
        return tooltipHtml(d, degrees.get(Number(d.gip)));
      },
    },
    // No ECharts legend: the section's own stage chips already carry the same
    // five colours PLUS a count and a filter action. Two legends for one
    // encoding is one legend too many, and the chart's copy was the weaker one.
    series: [{
      type: "graph",
      layout: "force",
      // 'move' not true: `roam: true` binds wheel-zoom, which fights page
      // scroll exactly as the timeline's dataZoom did. Panning by drag stays.
      roam: "move",
      // Dragging a node moved it and changed nothing — motion that looks like
      // it did something. The layout is the answer here, not a canvas to
      // rearrange.
      draggable: false,
      categories: GIP_STAGE_ORDER.map((name) => ({
        name,
        itemStyle: { color: STAGE_COLORS[name] },
      })),
      force: { repulsion: 220, edgeLength: [40, 160], gravity: 0.06 },
      label: {
        show: true,
        formatter: (p: unknown) => {
          const d = (p as { data?: Record<string, unknown> }).data ?? {};
          const inbound = degrees.get(Number(d.gip))?.inbound ?? 0;
          return inbound >= 4 || Number(d.gip) === focus ? `GIP-${d.gip}` : "";
        },
        fontFamily: LABEL_FONT,
        fontSize: 10,
      },
      emphasis: { focus: "adjacency", label: { show: true } },
      data: shown.map((n) => ({
        id: String(n.gip),
        name: `GIP-${n.gip}`,
        gip: n.gip,
        fullLabel: n.label,
        stage: n.stage,
        proposalState: n.proposalState,
        quorumStatus: n.quorumStatus,
        posts: n.posts,
        participants: n.participants,
        votes: n.votes,
        firstSeen: n.firstSeen,
        topicId: n.topicId,
        proposalId: n.proposalId,
        symbolSize: nodeSize(n.posts, Math.max(1, ...shown.map((x) => x.posts ?? 0))),
        category: Math.max(0, GIP_STAGE_ORDER.indexOf(n.stage)),
        itemStyle: n.gip === focus ? { borderColor: "#fff", borderWidth: 2 } : undefined,
      })),
      links: links.map((e) => ({
        source: String(e.src),
        target: String(e.dst),
        srcGip: e.src,
        dstGip: e.dst,
        weight: e.weight,
        lineStyle: {
          width: Math.min(5, 0.5 + Math.log2(e.weight + 1)),
          opacity: 0.4,
          curveness: 0.12,
        },
      })),
    }],
    _cerebro_height: "560px",
  } as EChartsOption;
}
