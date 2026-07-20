// Pure timeline machinery (vitest target — no React, no cosmos).
//
// The GraphModel for Timeline mode is built ONCE for the full range from the
// deduped timeline rows; the scrubber never touches topology. Each frame only
// derives per-link alpha/width (and per-node alpha) from the interval index —
// the canvas rewrites color/width buffers and re-renders WITHOUT touching the
// force simulation.
//
// Wire contract (TIMELINE_EDGES_COLUMNS): the first 7 columns are positionally
// identical to EDGES_COLUMNS, plus [7]=bucket_start, [8]=bucket_end as
// CH-bucketed ISO dates ("" = NULL/open):
//   flow      one row PER ACTIVE BUCKET (start == end), per-bucket weight
//   state     one row per edge, start .. "" (open; clamps to axis end)
//   interval  one row per validity span; "" end = still active
//   static    "" .. "" = always-on context (rendered dim, toggleable)

import type { GraphModel } from "./parseRows";

export interface TimelineInterval {
  /** Inclusive bucket-axis indices. */
  startIdx: number;
  endIdx: number;
  weight: number;
}

export interface TimelineIndex {
  /** Per model-link interval lists (aligned with model.linkIds order). */
  intervals: TimelineInterval[][];
  /** True for links whose ONLY rows are static (always-on context). */
  isStatic: boolean[];
  /** True for links whose profile is a FLOW (volume) shape — only these get
   * weight-scaled widths. State/interval edges are binary relationships:
   * scaling them made every trust edge max-width (all weights equal their
   * profile max) and turned dense clusters into an unreadable blob. */
  isFlow: boolean[];
  /** Per-profile max in-window weight over the range (width normalization —
   * raw weights across profiles have incompatible units). */
  profileMax: Map<string, number>;
  /** Link -> profile (denormalized from the model for frame computation). */
  linkProfile: string[];
  /** Per-bucket activity: number of non-static link-intervals covering it. */
  bucketActivity: number[];
}

export interface TimelineFrame {
  linkAlpha: Float32Array;
  linkWidth: Float32Array;
  pointAlpha: Float32Array;
}

/** Deterministic bucket axis stepped from the server's CH-bucketed
 * range_start up to (exclusive) range_end. UTC; ISO-Monday weeks match
 * `toStartOfWeek(x, 1)`, months are calendar months. */
export function buildBucketAxis(
  rangeStart: string,
  rangeEndExclusive: string,
  grain: string,
): string[] {
  if (!rangeStart || !rangeEndExclusive) return [];
  const out: string[] = [];
  let cur = new Date(`${rangeStart}T00:00:00Z`);
  const end = new Date(`${rangeEndExclusive}T00:00:00Z`);
  let guard = 0;
  while (cur < end && guard++ < 5000) {
    out.push(cur.toISOString().slice(0, 10));
    if (grain === "day") {
      cur = new Date(cur.getTime() + 86_400_000);
    } else if (grain === "week") {
      cur = new Date(cur.getTime() + 7 * 86_400_000);
    } else {
      cur = new Date(
        Date.UTC(cur.getUTCFullYear(), cur.getUTCMonth() + 1, 1),
      );
    }
  }
  return out;
}

/** Collapse timeline rows to one EDGES_COLUMNS-shaped row per edge id
 * (weights summed across buckets) — fed straight to buildGraphModel. */
export function dedupeTimelineEdges(rows: unknown[][] | undefined): unknown[][] {
  const byId = new Map<string, unknown[]>();
  for (const row of rows ?? []) {
    if (!Array.isArray(row) || !row[0]) continue;
    const id = String(row[0]);
    const existing = byId.get(id);
    if (existing) {
      const previousWeight = existing[4];
      const nextWeight = row[4];
      existing[4] =
        previousWeight == null || nextWeight == null
          ? null
          : Number(previousWeight) + Number(nextWeight);
      existing[5] = Number(existing[5] ?? 0) + Number(row[5] ?? 0);
    } else {
      byId.set(id, row.slice(0, 7));
    }
  }
  return Array.from(byId.values());
}

/** Build the per-link interval index for a model constructed from
 * dedupeTimelineEdges(rows). Rows whose edge id is not in the model (dangling
 * endpoints dropped by buildGraphModel) are ignored. */
export function buildTimelineIndex(
  rows: unknown[][] | undefined,
  model: GraphModel,
  axis: string[],
  profileShapes: Record<string, string> = {},
): TimelineIndex {
  const axisIdx = new Map<string, number>();
  axis.forEach((b, i) => axisIdx.set(b, i));
  const linkIdx = new Map<string, number>();
  model.linkIds.forEach((id, i) => linkIdx.set(id, i));
  const n = model.linkIds.length;
  const intervals: TimelineInterval[][] = Array.from({ length: n }, () => []);
  const isStatic: boolean[] = Array.from({ length: n }, () => true);
  const linkProfile: string[] = model.linkIds.map(
    (_, i) => model.edgeRows[i]?.profile ?? "",
  );
  const isFlow: boolean[] = linkProfile.map(
    (p) => profileShapes[p] === "flow",
  );
  const profileMax = new Map<string, number>();
  const bucketActivity = new Array<number>(axis.length).fill(0);
  const lastIdx = Math.max(0, axis.length - 1);

  const clampIdx = (bucket: string, fallback: number): number => {
    if (!bucket) return fallback;
    const hit = axisIdx.get(bucket);
    if (hit !== undefined) return hit;
    // Out-of-axis bucket (e.g. a state edge that began before the range):
    // clamp into the axis instead of dropping the row.
    return bucket < (axis[0] ?? "") ? 0 : lastIdx;
  };

  for (const row of rows ?? []) {
    if (!Array.isArray(row) || !row[0]) continue;
    const li = linkIdx.get(String(row[0]));
    if (li === undefined) continue;
    const bucketStart = String(row[7] ?? "");
    const bucketEnd = String(row[8] ?? "");
    const weight = Number(row[4] ?? 0) || 0;
    const staticRow = !bucketStart && !bucketEnd;
    if (!staticRow) isStatic[li] = false;
    const startIdx = staticRow ? 0 : clampIdx(bucketStart, 0);
    const endIdx = staticRow ? lastIdx : clampIdx(bucketEnd, lastIdx);
    intervals[li].push({ startIdx, endIdx: Math.max(startIdx, endIdx), weight });
    if (!staticRow) {
      const profile = linkProfile[li];
      profileMax.set(profile, Math.max(profileMax.get(profile) ?? 0, weight));
      for (let b = startIdx; b <= Math.min(endIdx, lastIdx); b++) {
        bucketActivity[b] += 1;
      }
    }
  }
  return { intervals, isStatic, isFlow, profileMax, linkProfile, bucketActivity };
}

const HIDDEN_NODE_ALPHA = 0.15;
const STATIC_ALPHA = 0.12;
const STATE_ALPHA = 0.5;
const STATE_WIDTH = 1.4;
const FLOW_ALPHA = 0.9;
const MIN_WIDTH = 1;
const MAX_WIDTH = 6;

/** Per-frame link/node visibility for the window [cursor, cursor+window). */
export function computeFrame(
  index: TimelineIndex,
  model: GraphModel,
  cursor: number,
  windowBuckets: number,
  opts: { showStatic: boolean },
): TimelineFrame {
  const n = index.intervals.length;
  const linkAlpha = new Float32Array(n);
  const linkWidth = new Float32Array(n);
  const pointAlpha = new Float32Array(model.n).fill(HIDDEN_NODE_ALPHA);
  const winStart = cursor;
  const winEnd = cursor + Math.max(1, windowBuckets) - 1; // inclusive

  const markEndpoints = (li: number) => {
    const s = model.idToIndex.get(model.edgeRows[li]?.source ?? "");
    const t = model.idToIndex.get(model.edgeRows[li]?.target ?? "");
    if (s !== undefined) pointAlpha[s] = 1;
    if (t !== undefined) pointAlpha[t] = 1;
  };

  for (let li = 0; li < n; li++) {
    if (index.isStatic[li]) {
      if (opts.showStatic) {
        linkAlpha[li] = STATIC_ALPHA;
        linkWidth[li] = MIN_WIDTH;
      }
      // Static context never lights endpoints by itself.
      continue;
    }
    let windowWeight = 0;
    let visible = false;
    for (const iv of index.intervals[li]) {
      if (iv.startIdx <= winEnd && iv.endIdx >= winStart) {
        visible = true;
        windowWeight += iv.weight;
      }
    }
    if (!visible) continue; // alpha 0 AND width 0 (also suppresses clicks)
    if (index.isFlow[li]) {
      // Volume edges: width pulses with in-window weight (per-profile scale).
      const max = index.profileMax.get(index.linkProfile[li]) ?? 0;
      const norm =
        max > 0 ? Math.log10(windowWeight + 1) / Math.log10(max + 1) : 1;
      linkAlpha[li] = FLOW_ALPHA;
      linkWidth[li] =
        MIN_WIDTH + Math.min(1, Math.max(0, norm)) * (MAX_WIDTH - MIN_WIDTH);
    } else {
      // Binary relationships (trust/ownership/labels): thin, semi-dim, so
      // the animated flows stay the foreground.
      linkAlpha[li] = STATE_ALPHA;
      linkWidth[li] = STATE_WIDTH;
    }
    markEndpoints(li);
  }
  return { linkAlpha, linkWidth, pointAlpha };
}
