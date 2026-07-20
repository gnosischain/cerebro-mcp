// THE single row-parsing module for the Graph Explorer. Every consumer
// (canvas, details panel, mode views) parses dataset rows through here —
// never inline.
//
// Node rows:  [id, kind, label, profiles[]]
// Edge rows:  [id, source, target, profile, weight, edge_count, directed]
// Evidence:   [owner_id, column, value, subject_kind, request_id]

import type {
  EvidenceExpectation,
  EvidenceRow,
  GraphEdgeRow,
  GraphNodeRow,
} from "../types";
import { colorForRelationship, FALLBACK_COLOR, hexToRgba } from "./palette";

export function parseNodeRow(row: unknown): GraphNodeRow | null {
  if (!Array.isArray(row)) return null;
  const [id, kind, label, profiles] = row as [unknown, unknown, unknown, unknown];
  if (id === undefined || id === null || String(id) === "") return null;
  return {
    id: String(id),
    kind: String(kind ?? "address"),
    label: String(label ?? ""),
    profiles: Array.isArray(profiles) ? profiles.map(String) : [],
  };
}

export function parseEdgeRow(row: unknown): GraphEdgeRow | null {
  if (!Array.isArray(row)) return null;
  const [id, source, target, profile, weight, edge_count, directed] = row as [
    unknown, unknown, unknown, unknown, unknown, unknown, unknown,
  ];
  if (!id || !source || !target) return null;
  return {
    id: String(id),
    source: String(source),
    target: String(target),
    profile: String(profile ?? ""),
    // NaN is the internal nullable sentinel: rendering can still give an
    // unpriced edge a minimum structural width, while every textual surface
    // can distinguish it from a genuine numeric zero.
    weight:
      weight === null || weight === undefined || weight === ""
        ? Number.NaN
        : Number(weight),
    edge_count: Number(edge_count ?? 0),
    directed: Boolean(directed),
  };
}

export function parseNodeRows(rows: unknown[][] | undefined): GraphNodeRow[] {
  const out: GraphNodeRow[] = [];
  for (const row of rows ?? []) {
    const parsed = parseNodeRow(row);
    if (parsed) out.push(parsed);
  }
  return out;
}

export function parseEdgeRows(rows: unknown[][] | undefined): GraphEdgeRow[] {
  const out: GraphEdgeRow[] = [];
  for (const row of rows ?? []) {
    const parsed = parseEdgeRow(row);
    if (parsed) out.push(parsed);
  }
  return out;
}

export function parseEvidenceRows(rows: unknown[][] | undefined): EvidenceRow[] {
  const parsed: EvidenceRow[] = [];
  for (const row of rows ?? []) {
    if (!Array.isArray(row)) continue;
    const ownerId = String(row[0] ?? "");
    const subjectKind = row[3] === "node" || row[3] === "edge" ? row[3] : null;
    const requestId = Number(row[4]);
    // Legacy three-column evidence is intentionally not renderable. Without
    // an owner kind + request id the client cannot prove which focus request
    // produced it, so accepting it would restore the wrong-object race.
    if (!ownerId || !subjectKind || !Number.isSafeInteger(requestId) || requestId < 0) {
      continue;
    }
    parsed.push({
      ownerId,
      column: String(row[1] ?? ""),
      value: String(row[2] ?? ""),
      subjectKind,
      requestId,
    });
  }
  return parsed;
}

/** Exact three-way evidence gate. `expected` is client intent, not the latest
 * payload to arrive, so resolving request A after the user selected B cannot
 * make A render while B is still queued or loading. */
export function filterEvidenceRows(
  rows: EvidenceRow[],
  expected: EvidenceExpectation | null,
): EvidenceRow[] {
  if (!expected?.subjectId) return [];
  return rows.filter(
    (row) =>
      row.subjectKind === expected.subjectKind &&
      row.ownerId === expected.subjectId &&
      row.requestId === expected.requestId,
  );
}

/** Cosmos simulation-space side length. Initial positions MUST live inside
 * [0..SPACE_SIZE] (centered) or the force sim drags the cloud toward the
 * space center and out of the fitted camera. Single source of truth for
 * parseRows seeding and the CosmosCanvas `spaceSize` config. 8192 gives a
 * multi-thousand-node cloud room to spread before hitting the space clamp
 * (4096 packed large graphs into a dense blob). */
export const SPACE_SIZE = 8192;

/** Everything the WebGL canvas + overlays need, precomputed once per
 * data/profile change. */
export interface GraphModel {
  n: number;
  nodeRows: GraphNodeRow[];
  /** Profile-filtered edges with both endpoints present (dangling dropped). */
  edgeRows: GraphEdgeRow[];
  positions: Float32Array;
  sizes: Float32Array;
  degrees: Float32Array;
  links: Float32Array;
  linkWidths: Float32Array;
  linkColors: Float32Array;
  linkArrows: boolean[];
  linkIds: string[];
  /** Every real edge id represented by each rendered link. Unlike `linkIds`
   * (the clickable head id), this lets selection find and highlight a bundle
   * when the selected edge is a non-head parallel member. */
  linkEdgeIds: string[][];
  /** Parallel same-direction edges bundled into each rendered link. 1 = a
   * lone edge. Lets the UI say "×N" instead of silently drawing N-1 lines
   * exactly on top of each other. */
  linkCounts: number[];
  /** Distinct edge profiles bundled into each rendered link. A link is hidden
   * only when EVERY profile in its bundle is hidden. Indexed by RENDERED link,
   * which after the parallel collapse is no longer 1:1 with `edgeRows`. */
  linkProfiles: string[][];
  idToIndex: Map<string, number>;
  indexToId: string[];
  profileColor: Map<string, string>;
  hubIndices: number[];
}

export interface BuildModelOptions {
  /** Bundle parallel SAME-DIRECTION edges into one rendered link.
   *
   * OFF by default, and that default is load-bearing: in Timeline mode
   * `edgeRows` holds one row per (pair, time bucket), so collapsing on
   * (source, target) would fuse every bucket into a single link and destroy
   * the playback. Enable it only where duplicates are a rendering artefact
   * rather than distinct data — Transactions (one edge per transfer leg) and
   * Flows (one edge per token between a pair).
   */
  collapseParallel?: boolean;
  /** `unresolved` permits the server-widening safety net while the client has
   * not adopted profile authority. Once `applied`, an empty/missing match is
   * an instruction to render no edges and no orphan nodes. */
  profileSelectionPhase?: "unresolved" | "applied";
}

export function buildGraphModel(
  nodeRowsRaw: unknown[][] | undefined,
  edgeRowsRaw: unknown[][] | undefined,
  activeProfiles: string[],
  options: BuildModelOptions = {},
): GraphModel {
  const allNodeRows = parseNodeRows(nodeRowsRaw);
  const activeSet = new Set(activeProfiles);
  const allEdgeRows = parseEdgeRows(edgeRowsRaw);
  const filteredEdgeRows = allEdgeRows.filter((e) => activeSet.has(e.profile));
  const profileSelectionPhase = options.profileSelectionPhase ?? "unresolved";
  // Safety net applies ONLY before the client has adopted authoritative
  // selection state. Once applied, an empty selection or a selection with no
  // matching rows is deliberate and must never widen back to every edge.
  const candidateEdges =
    profileSelectionPhase === "unresolved" &&
    (activeSet.size === 0 || filteredEdgeRows.length === 0) &&
    allEdgeRows.length > 0
      ? allEdgeRows
      : filteredEdgeRows;

  const allNodeIds = new Set(allNodeRows.map((node) => node.id));
  const nonDanglingCandidates = candidateEdges.filter(
    (edge) => allNodeIds.has(edge.source) && allNodeIds.has(edge.target),
  );
  const connectedIds = new Set(
    nonDanglingCandidates.flatMap((edge) => [edge.source, edge.target]),
  );
  const nodeRows =
    profileSelectionPhase === "applied"
      ? allNodeRows.filter((node) => connectedIds.has(node.id))
      : allNodeRows;

  const idToIndex = new Map<string, number>();
  nodeRows.forEach((n, i) => idToIndex.set(n.id, i));
  const indexToId = nodeRows.map((n) => n.id);

  // Drop dangling edges (endpoint not in the node set).
  const edgeRows = nonDanglingCandidates.filter(
    (e) => idToIndex.has(e.source) && idToIndex.has(e.target),
  );

  const n = nodeRows.length;
  const positions = new Float32Array(n * 2);
  const degrees = new Float32Array(n);

  // Circular initial layout (force sim relaxes from here; also used as-is
  // for the "circular" layout option). A wide radius + small radial jitter
  // gives the sim room to expand into a balanced cloud instead of starting
  // cramped and collapsing into a one-sided fan.
  //
  // CENTERED IN COSMOS SPACE: the simulation space is [0..spaceSize] with
  // gravity pulling toward its center — a ring around the ORIGIN sits mostly
  // outside the space, so running the sim dragged the whole cloud toward
  // (spaceSize/2, spaceSize/2) while the camera stayed fitted to the origin
  // bbox ("the graph disappears"). The radius is clamped so the ring always
  // fits inside the space, and the jitter is a deterministic hash of the
  // index so rebuilding the model does NOT re-randomize the layout.
  const center = SPACE_SIZE / 2;
  const radius = Math.min(SPACE_SIZE * 0.44, Math.max(260, n * 4));
  for (let i = 0; i < n; i++) {
    const a = (i / Math.max(1, n)) * Math.PI * 2;
    const jitter = 0.75 + (((i * 2654435761) >>> 16) % 1000) / 2000; // 0.75..1.25×
    positions[i * 2] = center + Math.cos(a) * radius * jitter;
    positions[i * 2 + 1] = center + Math.sin(a) * radius * jitter;
  }

  // Assign each profile a deterministic palette slot. The color is derived
  // from the relationship id rather than encounter order, so filtering,
  // pagination and reloads cannot silently change the legend's meaning.
  const profileColor = new Map<string, string>();
  const linkPairs: number[] = [];
  const linkWidths: number[] = [];
  const linkColors: number[] = [];
  const linkArrows: boolean[] = [];
  const linkIds: string[] = [];
  // Populate over EVERY edge so the legend is unaffected by the parallel-link
  // collapse below. Map insertion order may vary; the color itself cannot.
  for (const e of edgeRows) {
    if (!profileColor.has(e.profile)) {
      profileColor.set(e.profile, colorForRelationship(e.profile));
    }
  }
  const mixedWeightUnits = profileColor.size > 1;

  // Collapse SAME-DIRECTION parallel edges into one rendered link.
  //
  // Curved links separate a reciprocal pair (A→B vs B→A bow to opposite
  // sides), but cosmos derives the curve from the endpoints alone with a
  // GLOBAL control-point distance — so N edges A→B all draw the identical
  // arc and stack invisibly. Five transfers then look exactly like one,
  // which understates activity rather than merely looking untidy.
  //
  // `edgeRows` keeps every edge (details/selection/evidence are unaffected);
  // only the GPU buffers are collapsed, with the count carried in
  // `linkCounts` and encoded in the stroke width.
  let linkIdSeq = 0;
  const groups = new Map<string, { s: number; t: number; edges: GraphEdgeRow[] }>();
  for (const e of edgeRows) {
    const s = idToIndex.get(e.source)!;
    const t = idToIndex.get(e.target)!;
    // Without collapsing, every edge is its own group — index-for-index
    // identical to the pre-bundling behaviour, which Timeline depends on.
    const key = options.collapseParallel
      ? `${s}->${t}`
      : `#${linkIdSeq++}`;
    const g = groups.get(key);
    if (g) g.edges.push(e);
    else groups.set(key, { s, t, edges: [e] });
    // Degree still counts every edge — node size reflects real activity.
    degrees[s] += 1;
    degrees[t] += 1;
  }

  const linkCounts: number[] = [];
  const linkProfiles: string[][] = [];
  const linkEdgeIds: string[][] = [];
  for (const { s, t, edges } of groups.values()) {
    const head = edges[0];
    // Weight of the bundle, not of one arbitrary member.
    const weight = edges.reduce(
      (sum, edge) => sum + (Number.isFinite(edge.weight) ? edge.weight : 0),
      0,
    );
    // Raw weights from different relationship profiles have incompatible
    // units (USD, ownership edges, counts, scores). A mixed-profile canvas is
    // therefore categorical; quantitative width is reserved for a single
    // applied profile where every edge declares the same unit.
    const base = mixedWeightUnits
      ? 1.5
      : Math.max(1, Math.log10(Math.max(0, weight) + 1) * 1.2);
    // A bundle reads thicker, but sub-linearly so 50 dust transfers cannot
    // out-shout one large one.
    const bundled = base * (1 + Math.log10(edges.length) * 0.6);
    linkPairs.push(s, t);
    linkWidths.push(bundled);
    const [r, g, b] = hexToRgba(profileColor.get(head.profile) ?? FALLBACK_COLOR);
    linkColors.push(r, g, b, 0.75);
    linkArrows.push(edges.some((e) => e.directed));
    // A REAL edge id, so selecting the bundle opens genuine evidence.
    linkIds.push(head.id);
    linkCounts.push(edges.length);
    linkProfiles.push([...new Set(edges.map((e) => e.profile))]);
    linkEdgeIds.push(edges.map((edge) => edge.id));
  }

  // Degree-based node sizes: hubs visibly larger for clear contrast (3..16px)
  // while leaves stay small enough that they never occlude the edge web.
  // scalePointsOnZoom is off so tight clusters don't balloon when you zoom in.
  const sizes = new Float32Array(n);
  let maxDeg = 1;
  for (let i = 0; i < n; i++) maxDeg = Math.max(maxDeg, degrees[i]);
  for (let i = 0; i < n; i++) {
    sizes[i] = 3 + (Math.sqrt(degrees[i]) / Math.sqrt(maxDeg)) * 13;
  }

  // Top-degree hub indices (for always-on labels even on large graphs).
  const hubIndices = Array.from({ length: n }, (_, i) => i)
    .sort((a, b) => degrees[b] - degrees[a])
    .slice(0, Math.min(8, n));

  return {
    n,
    nodeRows,
    edgeRows,
    positions,
    sizes,
    degrees,
    links: new Float32Array(linkPairs),
    linkWidths: new Float32Array(linkWidths),
    linkColors: new Float32Array(linkColors),
    linkArrows,
    linkIds,
    linkEdgeIds,
    linkCounts,
    linkProfiles,
    idToIndex,
    indexToId,
    profileColor,
    hubIndices,
  };
}

/** Compact display form for ids ("0x1234…abcd"). */
export function shortId(id: string): string {
  if (id.startsWith("0x") && id.length > 16) {
    return `${id.slice(0, 8)}…${id.slice(-6)}`;
  }
  return id.length > 22 ? `${id.slice(0, 20)}…` : id;
}
