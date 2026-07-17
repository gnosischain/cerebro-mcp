// THE single row-parsing module for the Graph Explorer. Every consumer
// (canvas, details panel, mode views) parses dataset rows through here —
// never inline.
//
// Node rows:  [id, kind, label, profiles[]]
// Edge rows:  [id, source, target, profile, weight, edge_count, directed]
// Evidence:   [owner_id, column, value]

import type { EvidenceRow, GraphEdgeRow, GraphNodeRow } from "../types";
import { FALLBACK_COLOR, PROFILE_PALETTE, hexToRgba } from "./palette";

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
    weight: Number(weight ?? 0),
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
  return (rows ?? [])
    .filter((r) => Array.isArray(r))
    .map((r) => ({
      column: String(r[1] ?? ""),
      value: String(r[2] ?? ""),
    }));
}

/** Cosmos simulation-space side length. Initial positions MUST live inside
 * [0..SPACE_SIZE] (centered) or the force sim drags the cloud toward the
 * space center and out of the fitted camera. Single source of truth for
 * parseRows seeding and the CosmosCanvas `spaceSize` config. */
export const SPACE_SIZE = 4096;

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
  idToIndex: Map<string, number>;
  indexToId: string[];
  profileColor: Map<string, string>;
  hubIndices: number[];
}

export function buildGraphModel(
  nodeRowsRaw: unknown[][] | undefined,
  edgeRowsRaw: unknown[][] | undefined,
  activeProfiles: string[],
): GraphModel {
  const nodeRows = parseNodeRows(nodeRowsRaw);
  const activeSet = new Set(activeProfiles);
  const allEdgeRows = parseEdgeRows(edgeRowsRaw);
  const filteredEdgeRows = allEdgeRows.filter(
    (e) => !activeSet.size || activeSet.has(e.profile),
  );
  // Safety net: if the active-profile filter removed every edge but the
  // backend did return edges (e.g. it auto-widened to profiles the UI's
  // active set hasn't picked up yet), show them rather than a blank graph.
  const candidateEdges =
    filteredEdgeRows.length === 0 && allEdgeRows.length > 0
      ? allEdgeRows
      : filteredEdgeRows;

  const idToIndex = new Map<string, number>();
  nodeRows.forEach((n, i) => idToIndex.set(n.id, i));
  const indexToId = nodeRows.map((n) => n.id);

  // Drop dangling edges (endpoint not in the node set).
  const edgeRows = candidateEdges.filter(
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

  // Assign each profile a palette slot (first-seen order) for edge coloring.
  const profileColor = new Map<string, string>();
  const linkPairs: number[] = [];
  const linkWidths: number[] = [];
  const linkColors: number[] = [];
  const linkArrows: boolean[] = [];
  const linkIds: string[] = [];
  for (const e of edgeRows) {
    const s = idToIndex.get(e.source)!;
    const t = idToIndex.get(e.target)!;
    if (!profileColor.has(e.profile)) {
      profileColor.set(
        e.profile,
        PROFILE_PALETTE[profileColor.size % PROFILE_PALETTE.length],
      );
    }
    linkPairs.push(s, t);
    linkWidths.push(Math.max(1, Math.log10((e.weight || 1) + 1) * 1.2));
    const [r, g, b] = hexToRgba(profileColor.get(e.profile) ?? FALLBACK_COLOR);
    linkColors.push(r, g, b, 0.75);
    linkArrows.push(Boolean(e.directed));
    linkIds.push(e.id);
    degrees[s] += 1;
    degrees[t] += 1;
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
