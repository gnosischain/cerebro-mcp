import { Graph } from "@cosmos.gl/graph";
import { useEffect, useMemo, useRef, useState } from "react";
import type { DatasetDescriptor } from "../shared/miniAppTypes";
import type { GraphEdgeRow, GraphNodeRow } from "./types";

interface Props {
  nodes?: DatasetDescriptor;
  edges?: DatasetDescriptor;
  selectedNodeId: string;
  selectedEdgeId: string;
  seedNodeId?: string;
  activeProfiles: string[];
  layout: "force" | "circular";
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onExpandNode: (id: string) => void;
}

const COLOR_BY_KIND: Record<string, string> = {
  address: "#6ee7b7",
  safe: "#a78bfa",
  gpay_wallet: "#fbbf24",
  circles_avatar: "#60a5fa",
  circles_wrapper: "#38bdf8",
  token: "#f472b6",
  pool: "#c084fc",
  validator: "#f97316",
  bridge: "#facc15",
  project_label: "#94a3b8",
};
const FALLBACK_COLOR = "#9ca3af";
const SEED_COLOR = "#fde047"; // bright gold — the seed must pop out.

// A stable palette for edge-by-profile coloring. Profiles are assigned a slot
// in first-seen order so the legend stays consistent within a session.
const PROFILE_PALETTE = [
  "#60a5fa", "#f472b6", "#34d399", "#fbbf24", "#a78bfa",
  "#fb7185", "#38bdf8", "#facc15", "#c084fc", "#4ade80",
  "#f97316", "#22d3ee", "#e879f9", "#94a3b8",
];

function parseNodeRow(row: unknown[]): GraphNodeRow {
  const [id, kind, label, profiles] = row as [string, string, string, string[] | null];
  return {
    id: String(id ?? ""),
    kind: String(kind ?? "address"),
    label: String(label ?? ""),
    profiles: Array.isArray(profiles) ? (profiles as string[]) : [],
  };
}

function parseEdgeRow(row: unknown[]): GraphEdgeRow {
  const [id, source, target, profile, weight, edge_count, directed] = row as [
    string, string, string, string, number, number, boolean
  ];
  return {
    id: String(id ?? ""),
    source: String(source ?? ""),
    target: String(target ?? ""),
    profile: String(profile ?? ""),
    weight: Number(weight ?? 0),
    edge_count: Number(edge_count ?? 0),
    directed: Boolean(directed),
  };
}

/** "#rrggbb" → [r,g,b,a] floats in 0..1 for Cosmos. */
function hexToRgba(hex: string, alpha = 1): [number, number, number, number] {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16) / 255;
  const g = parseInt(h.slice(2, 4), 16) / 255;
  const b = parseInt(h.slice(4, 6), 16) / 255;
  return [r, g, b, alpha];
}

function detectWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return !!(
      window.WebGLRenderingContext &&
      (canvas.getContext("webgl") || canvas.getContext("experimental-webgl"))
    );
  } catch {
    return false;
  }
}

export function CosmosGraph({
  nodes,
  edges,
  selectedNodeId,
  seedNodeId,
  activeProfiles,
  layout,
  onSelectNode,
  onSelectEdge,
  onExpandNode,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const labelLayerRef = useRef<HTMLDivElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<Graph | null>(null);
  const [webglOk] = useState(detectWebGL);
  // double-click detection (Cosmos exposes single-click only)
  const lastClickRef = useRef<{ index: number; t: number }>({ index: -1, t: 0 });

  // UI control state.
  const [search, setSearch] = useState("");
  const [searchMiss, setSearchMiss] = useState(false);
  const [focusMode, setFocusMode] = useState(false);
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(new Set());
  const [legendOpen, setLegendOpen] = useState(true);
  // Simulation running state — drives the play/pause control. The sim now
  // settles naturally (no hard timer freeze); the user can re-energize it to
  // watch the layout evolve, or pause it on demand.
  const [simRunning, setSimRunning] = useState(false);
  // Hover tooltip is the primary label affordance, so always-on labels default
  // to "off" — 100+ overlapping text pills hide the topology. The All/Auto/Off
  // control stays as an opt-in for users who want persistent labels.
  const [labelMode, setLabelMode] = useState<"all" | "auto" | "off">("off");

  // Parse + index the graph once per data/profile change.
  const model = useMemo(() => {
    const nodeRows = (nodes?.preview_rows ?? []).map(parseNodeRow);
    const activeSet = new Set(activeProfiles);
    const allEdgeRows = (edges?.preview_rows ?? []).map(parseEdgeRow);
    const filteredEdgeRows = allEdgeRows.filter(
      (e) => !activeSet.size || activeSet.has(e.profile),
    );
    // Safety net: if the active-profile filter removed every edge but the
    // backend did return edges (e.g. it auto-widened to profiles the UI's
    // active set hasn't picked up yet), show them rather than a blank graph.
    const edgeRows =
      filteredEdgeRows.length === 0 && allEdgeRows.length > 0
        ? allEdgeRows
        : filteredEdgeRows;

    const idToIndex = new Map<string, number>();
    nodeRows.forEach((n, i) => idToIndex.set(n.id, i));
    const indexToId = nodeRows.map((n) => n.id);

    const n = nodeRows.length;
    const positions = new Float32Array(n * 2);
    const degrees = new Float32Array(n);

    // Circular initial layout (force sim relaxes from here; also used as-is
    // for the "circular" layout option). A wide radius + small radial jitter
    // gives the sim room to expand into a balanced cloud instead of starting
    // cramped and collapsing into a one-sided fan.
    const radius = Math.max(260, n * 4);
    for (let i = 0; i < n; i++) {
      const a = (i / Math.max(1, n)) * Math.PI * 2;
      const jitter = 0.75 + Math.random() * 0.5; // 0.75..1.25× radius
      positions[i * 2] = Math.cos(a) * radius * jitter;
      positions[i * 2 + 1] = Math.sin(a) * radius * jitter;
    }

    // Assign each profile a palette slot (first-seen order) for edge coloring.
    const profileColor = new Map<string, string>();
    const linkPairs: number[] = [];
    const linkWidths: number[] = [];
    const linkColors: number[] = [];
    const linkArrows: boolean[] = [];
    const linkIds: string[] = [];
    for (const e of edgeRows) {
      const s = idToIndex.get(e.source);
      const t = idToIndex.get(e.target);
      if (s === undefined || t === undefined) continue;
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
  }, [nodes, edges, activeProfiles]);

  // The Cosmos Graph is created once (on [webglOk]); its click/tick callbacks
  // capture whatever is in scope at creation time — i.e. the initial EMPTY
  // model. Reading the live model + callbacks through refs (updated every
  // render) is what makes click-select / double-click-expand / edge-select and
  // label positioning actually work after data loads.
  const modelRef = useRef(model);
  const cbRef = useRef({ onSelectNode, onSelectEdge, onExpandNode });
  useEffect(() => {
    modelRef.current = model;
  }, [model]);
  useEffect(() => {
    cbRef.current = { onSelectNode, onSelectEdge, onExpandNode };
  });

  // Compute node colors honoring seed highlight + hidden-kind dimming. Returns
  // a Float32Array(n*4). Recomputed whenever the model, seed, or hidden kinds
  // change so the legend toggles take effect without a full rebuild.
  const colors = useMemo(() => {
    const out = new Float32Array(model.n * 4);
    for (let i = 0; i < model.n; i++) {
      const node = model.nodeRows[i];
      const isSeed = seedNodeId && node.id === seedNodeId;
      const hex = isSeed ? SEED_COLOR : COLOR_BY_KIND[node.kind] ?? FALLBACK_COLOR;
      const hidden = hiddenKinds.has(node.kind) && !isSeed;
      const [r, g, b, a] = hexToRgba(hex, hidden ? 0.04 : 1);
      out[i * 4] = r;
      out[i * 4 + 1] = g;
      out[i * 4 + 2] = b;
      out[i * 4 + 3] = a;
    }
    return out;
  }, [model, seedNodeId, hiddenKinds]);

  // Node sizes with a distinct seed marker: the seed is forced to a large
  // floor so it pops out of the cloud even when it isn't the top-degree hub.
  const sizes = useMemo(() => {
    const out = Float32Array.from(model.sizes);
    if (seedNodeId) {
      const si = model.idToIndex.get(seedNodeId);
      if (si !== undefined) out[si] = Math.max(out[si], 20);
    }
    return out;
  }, [model, seedNodeId]);

  // --- Label overlay -------------------------------------------------------
  // Cosmos GL is a GPU point/line renderer with no native text. We position
  // absolutely-placed HTML labels for the most interpretable nodes (seed,
  // selected, selected's neighbors, top-degree hubs) and reposition them on
  // every simulation tick / zoom via tracked screen positions.
  const labelIndicesRef = useRef<number[]>([]);
  const updateLabels = () => {
    const graph = graphRef.current;
    const layer = labelLayerRef.current;
    if (!graph || !layer) return;
    const m = modelRef.current;
    const indices = labelIndicesRef.current;
    if (!indices.length) {
      if (layer.childElementCount) layer.replaceChildren();
      return;
    }
    // Resolve a node's [x,y] space coordinate. Prefer the tracked-positions
    // map (the documented overlay API — refreshed every simulation tick), and
    // fall back to the full getPointPositions() readback for any index the map
    // doesn't yet hold (e.g. before the first tick or after a pause). Returns
    // null when neither source has the point yet.
    const tracked = graph.getTrackedPointPositionsMap();
    const allPositions = tracked.size ? null : graph.getPointPositions();
    const coordOf = (idx: number): [number, number] | null => {
      const t = tracked.get(idx);
      if (t) return t;
      if (allPositions) {
        const x = allPositions[idx * 2];
        const y = allPositions[idx * 2 + 1];
        if (x !== undefined && y !== undefined) return [x, y];
      }
      return null;
    };
    // Reconcile child divs with the index set (create/reuse keyed by data-idx).
    const existing = new Map<string, HTMLElement>();
    for (const child of Array.from(layer.children) as HTMLElement[]) {
      existing.set(child.dataset.idx ?? "", child);
    }
    const seen = new Set<string>();
    for (const idx of indices) {
      const coord = coordOf(idx);
      if (!coord) continue;
      const [sx, sy] = graph.spaceToScreenPosition(coord);
      const key = String(idx);
      seen.add(key);
      let el = existing.get(key);
      if (!el) {
        el = document.createElement("div");
        el.className = "ge-node-label";
        el.dataset.idx = key;
        el.textContent = m.nodeRows[idx]?.label || m.indexToId[idx] || "";
        layer.appendChild(el);
      }
      el.style.transform = `translate(-50%, -150%) translate(${sx}px, ${sy}px)`;
    }
    for (const [key, el] of existing) {
      if (!seen.has(key)) el.remove();
    }
  };

  // Re-track the currently-labelled indices against the graph's live point
  // positions, then repaint. Must run AFTER setPointPositions (Cosmos only
  // tracks points that already exist), so the data-push effect calls this too.
  const retrackLabels = () => {
    const graph = graphRef.current;
    if (!graph) return;
    graph.trackPointPositionsByIndices(labelIndicesRef.current);
    updateLabels();
  };

  // Decide which nodes get labels. Modes:
  //   off  → none
  //   all  → every node (hard-capped on very large graphs; falls back to the
  //          curated subset above the cap to stay legible/performant)
  //   auto → curated subset: seed + selection + its neighbors + top-degree hubs
  const LABEL_ALL_CAP = 1500;
  useEffect(() => {
    // NOTE: this effect is defined before the graph-create effect, so on the
    // first mount graphRef.current is still null. We must populate
    // labelIndicesRef regardless — the data-push effect (which runs after the
    // graph exists) re-tracks and paints. Only the graph-specific adjacency
    // lookup is guarded on the graph being present.
    if (!model.n) {
      labelIndicesRef.current = [];
      return;
    }
    const graph = graphRef.current;
    let set: Set<number>;
    if (labelMode === "off") {
      set = new Set<number>();
    } else if (labelMode === "all" && model.n <= LABEL_ALL_CAP) {
      set = new Set<number>(Array.from({ length: model.n }, (_, i) => i));
    } else {
      // "auto", or "all" above the cap: curated interpretable subset.
      set = new Set<number>(model.hubIndices);
    }
    if (labelMode !== "off") {
      if (seedNodeId !== undefined) {
        const si = model.idToIndex.get(seedNodeId);
        if (si !== undefined) set.add(si);
      }
      const selIdx = selectedNodeId ? model.idToIndex.get(selectedNodeId) : undefined;
      if (selIdx !== undefined) {
        set.add(selIdx);
        const adj = graph?.getAdjacentIndices(selIdx);
        if (adj) adj.forEach((a) => set.add(a));
      }
    }
    labelIndicesRef.current = Array.from(set);
    retrackLabels();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model, seedNodeId, selectedNodeId, labelMode]);

  // Create the Cosmos graph once.
  useEffect(() => {
    if (!webglOk || !containerRef.current) return;
    const graph = new Graph(containerRef.current, {
      backgroundColor: [0, 0, 0, 0],
      spaceSize: 4096,
      pointSize: 3,
      pointSizeScale: 1,
      linkColor: [0.55, 0.6, 0.7, 0.6],
      linkWidth: 1,
      linkWidthScale: 1,
      // Straight links so arrowheads read cleanly (curvature interferes with
      // arrow placement in 2.6.4).
      curvedLinks: false,
      linkArrows: true,
      linkArrowsSizeScale: 0.8,
      // Keep edges legible across zoom levels — Cosmos defaults to [50,150]
      // which fades any link longer than 150px on screen down to 0.25 alpha,
      // making edges look "missing" on a spread-out graph.
      linkVisibilityDistanceRange: [20, 600],
      linkVisibilityMinTransparency: 0.6,
      // Don't inflate points when zooming into a tight cluster — that's what
      // made dots balloon to ~50px and occlude the whole edge web.
      scalePointsOnZoom: false,
      fitViewOnInit: true,
      fitViewDelay: 300,
      hoveredPointCursor: "pointer",
      // Focus mode relies on selection greyout: non-selected points/links dim.
      pointGreyoutOpacity: 0.12,
      linkGreyoutOpacity: 0.06,
      // Re-tuned for a SNAPPY, smooth settle (no hard timer freeze). Stronger
      // link spring + higher friction pull the layout to equilibrium quickly
      // and damp thrashing; a faster decay (higher = cools faster; default
      // 5000) reaches a stable frame in ~6-10s and ends naturally via
      // onSimulationEnd. Repulsion + a long link distance still spread leaves
      // into a balanced cloud around a centered seed; low gravity keeps it
      // framed without crushing it to a point.
      simulationFriction: 0.9,
      simulationGravity: 0.08,
      simulationCenter: 0,
      simulationRepulsion: 1.6,
      simulationRepulsionTheta: 1.15,
      simulationLinkSpring: 0.5,
      simulationLinkDistance: 60,
      simulationDecay: 4000,
      onSimulationTick: () => updateLabels(),
      onSimulationStart: () => setSimRunning(true),
      onSimulationPause: () => setSimRunning(false),
      onSimulationUnpause: () => setSimRunning(true),
      onSimulationEnd: () => {
        setSimRunning(false);
        graphRef.current?.fitView(400);
        updateLabels();
      },
      onZoom: () => updateLabels(),
      onPointClick: (index: number) => {
        const now = Date.now();
        const last = lastClickRef.current;
        const id = modelRef.current.indexToId[index];
        if (last.index === index && now - last.t < 320) {
          if (id) cbRef.current.onExpandNode(id);
          lastClickRef.current = { index: -1, t: 0 };
          return;
        }
        lastClickRef.current = { index, t: now };
        if (id) cbRef.current.onSelectNode(id);
      },
      onLinkClick: (linkIndex: number) => {
        const id = modelRef.current.linkIds[linkIndex];
        if (id) cbRef.current.onSelectEdge(id);
      },
      // Hover tooltip — the primary way to read a node's address/label without
      // covering the whole graph in always-on text. Derive the screen position
      // from the point's space coordinate so it tracks the node precisely
      // regardless of the (variably-typed) DOM/D3 event.
      onPointMouseOver: (index: number, pointPosition: [number, number]) => {
        const tip = tooltipRef.current;
        const graph = graphRef.current;
        const m = modelRef.current;
        if (!tip || !graph) return;
        const node = m.nodeRows[index];
        if (!node) return;
        const [sx, sy] = graph.spaceToScreenPosition(pointPosition);
        tip.textContent = node.label || node.id;
        tip.style.left = `${sx}px`;
        tip.style.top = `${sy}px`;
        tip.classList.add("visible");
      },
      onPointMouseOut: () => {
        tooltipRef.current?.classList.remove("visible");
      },
    });
    graphRef.current = graph;
    return () => {
      graph.destroy();
      graphRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [webglOk]);

  // Keep the WebGL canvas sized to its container. Cosmos sizes to the canvas
  // but only re-measures on an explicit render()/fitView() — it has no internal
  // ResizeObserver — so toggling the details panel (the .ge-body grid flips
  // 1fr 320px ↔ 1fr 0) or resizing the desktop window left the canvas at its
  // old width and clipped on the right. Observe the wrapper and re-measure on
  // every size change (rAF-debounced), refitting so the graph stays framed.
  useEffect(() => {
    const wrap = containerRef.current?.parentElement;
    if (!wrap || typeof ResizeObserver === "undefined") return;
    let raf = 0;
    const ro = new ResizeObserver(() => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const graph = graphRef.current;
        if (!graph) return;
        graph.render();
        graph.fitView(300);
        updateLabels();
      });
    });
    ro.observe(wrap);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [webglOk]);

  // Play/pause the force simulation. When settled/paused, start() re-energizes
  // it (a fresh cooling cycle the user can watch); when running, pause() halts.
  const toggleSim = () => {
    const graph = graphRef.current;
    if (!graph) return;
    if (graph.isSimulationRunning) {
      graph.pause();
    } else {
      graph.start();
    }
  };

  // Push data whenever the model changes.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !model.n) return;
    graph.setPointPositions(model.positions);
    graph.setPointColors(colors);
    graph.setPointSizes(sizes);
    graph.setLinks(model.links);
    if (model.linkWidths.length) graph.setLinkWidths(model.linkWidths);
    if (model.linkColors.length) graph.setLinkColors(model.linkColors);
    if (model.linkArrows.length) graph.setLinkArrows(model.linkArrows);
    // Cosmos only tracks points that already exist — re-track the label set now
    // that positions are set, otherwise getTrackedPointPositionsMap() is empty
    // and no labels ever paint.
    retrackLabels();
    const timers: number[] = [];
    if (layout === "circular") {
      // Keep the circular seed positions; don't run the force sim.
      graph.setPointPositions(model.positions, true);
      graph.render();
      graph.fitView(400);
      timers.push(window.setTimeout(retrackLabels, 450));
    } else {
      graph.start();
      // Stage refits as the force layout expands so the graph never drifts
      // out of view before it settles. No hard pause timer — the tuned decay
      // cools the sim to a natural stop (onSimulationEnd does the final fit +
      // label repaint), which the user can re-energize via the play control.
      timers.push(window.setTimeout(() => graph.fitView(400), 300));
      timers.push(window.setTimeout(() => graph.fitView(400), 1200));
      timers.push(window.setTimeout(() => graph.fitView(400), 3000));
      timers.push(window.setTimeout(retrackLabels, 3100));
    }
    return () => timers.forEach((t) => window.clearTimeout(t));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model, layout]);

  // Recolor on seed/hidden-kind change without rebuilding the whole graph.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !model.n) return;
    graph.setPointColors(colors);
    graph.render();
  }, [colors, model.n]);

  // Resize the seed marker on seed change without rebuilding the whole graph.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !model.n) return;
    graph.setPointSizes(sizes);
    graph.render();
  }, [sizes, model.n]);

  // Reflect external selection + focus mode into Cosmos.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    const idx = selectedNodeId ? model.idToIndex.get(selectedNodeId) : undefined;
    if (idx === undefined) {
      graph.unselectPoints();
      return;
    }
    if (focusMode) {
      // Isolate selected + neighbors: select the whole neighborhood so the
      // built-in greyout dims everything else.
      const adj = graph.getAdjacentIndices(idx) ?? [];
      graph.selectPointsByIndices([idx, ...adj]);
    } else {
      graph.selectPointByIndex(idx, true);
    }
  }, [selectedNodeId, focusMode, model]);

  // Search → find by id/label substring, select + zoom.
  const runSearch = () => {
    const graph = graphRef.current;
    const q = search.trim().toLowerCase();
    if (!graph || !q) return;
    let hitIdx = -1;
    // exact id first, then substring on id/label
    const exact = model.idToIndex.get(q) ?? model.idToIndex.get(search.trim());
    if (exact !== undefined) {
      hitIdx = exact;
    } else {
      for (let i = 0; i < model.n; i++) {
        const node = model.nodeRows[i];
        if (
          node.id.toLowerCase().includes(q) ||
          node.label.toLowerCase().includes(q)
        ) {
          hitIdx = i;
          break;
        }
      }
    }
    if (hitIdx >= 0) {
      setSearchMiss(false);
      const id = model.indexToId[hitIdx];
      if (id) onSelectNode(id);
      graph.zoomToPointByIndex(hitIdx, 600, 4);
    } else {
      setSearchMiss(true);
      window.setTimeout(() => setSearchMiss(false), 1800);
    }
  };

  const toggleKind = (kind: string) => {
    setHiddenKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  };

  // Kinds actually present in the current graph (for the legend).
  const presentKinds = useMemo(() => {
    const set = new Set<string>();
    model.nodeRows.forEach((n) => set.add(n.kind));
    return Array.from(set).sort();
  }, [model]);

  // Profiles present (for the edge-color legend).
  const presentProfiles = useMemo(
    () => Array.from(model.profileColor.entries()).sort((a, b) => a[0].localeCompare(b[0])),
    [model],
  );

  if (!webglOk) {
    return (
      <div className="ge-placeholder">
        <span>
          WebGL is unavailable in this browser, so the graph canvas can't render.
          Try a hardware-accelerated browser or enable WebGL.
        </span>
      </div>
    );
  }

  if (!model.n) {
    return (
      <div className="ge-placeholder">
        <span>No nodes yet — seed an address from the catalog.</span>
      </div>
    );
  }

  return (
    <div className="ge-cosmos-wrap">
      <div ref={containerRef} className="ge-cosmos-canvas" />
      <div ref={labelLayerRef} className="ge-label-layer" aria-hidden />
      <div ref={tooltipRef} className="ge-node-tooltip" aria-hidden />

      {/* Top-left: search + view controls */}
      <div className="ge-graph-controls">
        <div className={`ge-graph-search ${searchMiss ? "miss" : ""}`}>
          <input
            type="text"
            placeholder={searchMiss ? "No match" : "Find node by address/label…"}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              if (searchMiss) setSearchMiss(false);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") runSearch();
            }}
          />
          <button type="button" onClick={runSearch} title="Find & zoom">
            ⌕
          </button>
        </div>
        <button
          type="button"
          className="ge-graph-btn"
          onClick={() => graphRef.current?.fitView(500)}
          title="Zoom to fit"
        >
          Fit
        </button>
        <button
          type="button"
          className="ge-graph-btn"
          onClick={() => {
            const g = graphRef.current;
            if (!g) return;
            g.setZoomLevel?.(1);
            g.fitView(500);
          }}
          title="Recenter"
        >
          Recenter
        </button>
        <button
          type="button"
          className={`ge-graph-btn ${focusMode ? "active" : ""}`}
          onClick={() => setFocusMode((v) => !v)}
          title="Focus mode — isolate the selected node and its neighbors"
          aria-pressed={focusMode}
        >
          Focus
        </button>
        <button
          type="button"
          className={`ge-graph-btn ${simRunning ? "active" : ""}`}
          onClick={toggleSim}
          title={
            simRunning
              ? "Pause the layout simulation"
              : "Play — re-energize the layout and watch it evolve"
          }
          aria-pressed={simRunning}
        >
          {simRunning ? "❚❚ Pause" : "▶ Play"}
        </button>
        <div className="ge-label-mode" role="group" aria-label="Label visibility">
          <span className="ge-label-mode-caption">Labels</span>
          {(["all", "auto", "off"] as const).map((m) => (
            <button
              key={m}
              type="button"
              className={`ge-graph-btn ${labelMode === m ? "active" : ""}`}
              onClick={() => setLabelMode(m)}
              aria-pressed={labelMode === m}
            >
              {m === "all" ? "All" : m === "auto" ? "Auto" : "Off"}
            </button>
          ))}
        </div>
      </div>

      {/* Bottom-left: legend (node kinds toggle + edge-by-profile colors) */}
      <div className={`ge-legend ${legendOpen ? "open" : "collapsed"}`}>
        <button
          type="button"
          className="ge-legend-toggle"
          onClick={() => setLegendOpen((v) => !v)}
        >
          {legendOpen ? "Legend ▾" : "Legend ▸"}
        </button>
        {legendOpen && (
          <div className="ge-legend-body">
            <div className="ge-legend-section">
              <div className="ge-legend-title">Node kinds (click to toggle)</div>
              {presentKinds.map((kind) => {
                const hidden = hiddenKinds.has(kind);
                return (
                  <button
                    key={kind}
                    type="button"
                    className={`ge-legend-item ${hidden ? "off" : ""}`}
                    onClick={() => toggleKind(kind)}
                  >
                    <span
                      className="ge-legend-swatch"
                      style={{ background: COLOR_BY_KIND[kind] ?? FALLBACK_COLOR }}
                    />
                    {kind}
                  </button>
                );
              })}
            </div>
            {presentProfiles.length > 0 && (
              <div className="ge-legend-section">
                <div className="ge-legend-title">Edge profiles</div>
                {presentProfiles.map(([profile, color]) => (
                  <div key={profile} className="ge-legend-item static">
                    <span
                      className="ge-legend-swatch line"
                      style={{ background: color }}
                    />
                    {profile}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
