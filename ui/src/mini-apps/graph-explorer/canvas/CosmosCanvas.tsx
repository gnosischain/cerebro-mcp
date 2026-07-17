// The pure WebGL renderer (extracted from the old CosmosGraph — the engine
// code is MOVED, not rewritten). It owns the @cosmos.gl/graph instance and
// nothing else: labels/tooltips live in LabelsOverlay (reached through
// `overlayRef`), controls in CanvasToolbar, composition in GraphCanvas.

import { Graph } from "@cosmos.gl/graph";
import { useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { SPACE_SIZE, type GraphModel } from "../model/parseRows";
import { COLOR_BY_KIND, FALLBACK_COLOR, SEED_COLOR, hexToRgba } from "../model/palette";
import type { GraphLayout } from "../types";

/** Imperative surface LabelsOverlay registers so the graph callbacks (tick /
 * zoom / data push) can drive label + tooltip painting without re-creating
 * the Graph. */
export interface CanvasOverlayHandle {
  updateLabels: () => void;
  retrackLabels: () => void;
  showTooltip: (index: number, pointPosition: [number, number]) => void;
  hideTooltip: () => void;
}

interface Props {
  model: GraphModel;
  selectedNodeId: string;
  seedNodeId?: string;
  focusMode: boolean;
  layout: GraphLayout;
  hiddenKinds: Set<string>;
  /** Owned by GraphCanvas; shared with the toolbar (fit/recenter/search). */
  graphRef: MutableRefObject<Graph | null>;
  overlayRef: MutableRefObject<CanvasOverlayHandle | null>;
  emptyHint: string;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onExpandNode: (id: string) => void;
  /** Background (non-node, non-edge) click. */
  onViewClick?: () => void;
  onSimRunningChange?: (running: boolean) => void;
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

export function CosmosCanvas({
  model,
  selectedNodeId,
  seedNodeId,
  focusMode,
  layout,
  hiddenKinds,
  graphRef,
  overlayRef,
  emptyHint,
  onSelectNode,
  onSelectEdge,
  onExpandNode,
  onViewClick,
  onSimRunningChange,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [webglOk] = useState(detectWebGL);
  // double-click detection (Cosmos exposes single-click only)
  const lastClickRef = useRef<{ index: number; t: number }>({ index: -1, t: 0 });
  // camera-follow throttle while the simulation runs
  const lastFollowRef = useRef(0);

  // The Cosmos Graph is created once (on [webglOk]); its click/tick callbacks
  // capture whatever is in scope at creation time — i.e. the initial EMPTY
  // model. Reading the live model + callbacks through refs (updated every
  // render) is what makes click-select / double-click-expand / edge-select and
  // label positioning actually work after data loads.
  const modelRef = useRef(model);
  const cbRef = useRef({
    onSelectNode,
    onSelectEdge,
    onExpandNode,
    onViewClick,
    onSimRunningChange,
  });
  useEffect(() => {
    modelRef.current = model;
  }, [model]);
  useEffect(() => {
    cbRef.current = {
      onSelectNode,
      onSelectEdge,
      onExpandNode,
      onViewClick,
      onSimRunningChange,
    };
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

  // Create the Cosmos graph once. NOTE: the container div is ALWAYS rendered
  // (the empty placeholder overlays it) so this create-once effect never runs
  // against a missing container when the first payload has zero nodes.
  useEffect(() => {
    if (!webglOk || !containerRef.current) return;
    const graph = new Graph(containerRef.current, {
      backgroundColor: [0, 0, 0, 0],
      spaceSize: SPACE_SIZE,
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
      // and damp thrashing. Decay is the alpha half-life in TICKS: 4000 meant
      // ~66s at 60fps before onSimulationEnd (and its rescue fitView) ever
      // fired — 1000 settles in ~15s. Repulsion + a long link distance still
      // spread leaves into a balanced cloud around a centered seed; low
      // gravity keeps it framed without crushing it to a point.
      simulationFriction: 0.9,
      simulationGravity: 0.08,
      simulationCenter: 0,
      simulationRepulsion: 1.6,
      simulationRepulsionTheta: 1.15,
      simulationLinkSpring: 0.5,
      simulationLinkDistance: 60,
      simulationDecay: 1000,
      // Camera-follow while the sim runs. One-shot staged refits cannot cover
      // a layout that keeps moving for many seconds — on large graphs the
      // cloud walked out of the fitted viewport and "disappeared". Throttled
      // so the chase stays smooth; when the sim is idle no ticks fire, so
      // manual pan/zoom is never fought.
      onSimulationTick: () => {
        overlayRef.current?.updateLabels();
        const now = performance.now();
        if (now - lastFollowRef.current > 800) {
          lastFollowRef.current = now;
          graphRef.current?.fitView(700);
        }
      },
      onSimulationStart: () => cbRef.current.onSimRunningChange?.(true),
      onSimulationPause: () => cbRef.current.onSimRunningChange?.(false),
      onSimulationUnpause: () => cbRef.current.onSimRunningChange?.(true),
      onSimulationEnd: () => {
        cbRef.current.onSimRunningChange?.(false);
        graphRef.current?.fitView(400);
        overlayRef.current?.updateLabels();
      },
      onZoom: () => overlayRef.current?.updateLabels(),
      onBackgroundClick: () => {
        cbRef.current.onViewClick?.();
      },
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
      // covering the whole graph in always-on text. The overlay derives the
      // screen position from the point's space coordinate so it tracks the
      // node precisely regardless of the (variably-typed) DOM/D3 event.
      onPointMouseOver: (index: number, pointPosition: [number, number]) => {
        overlayRef.current?.showTooltip(index, pointPosition);
      },
      onPointMouseOut: () => {
        overlayRef.current?.hideTooltip();
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
        overlayRef.current?.updateLabels();
      });
    });
    ro.observe(wrap);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [webglOk]);

  // Push data whenever the model changes. Layout continuity rules:
  //  - Nodes that were already on canvas KEEP their live simulated positions
  //    (only genuinely new nodes take the seeded ring position) — without
  //    this, every expand/hydration page rebuilt the model and scrambled the
  //    whole layout back to the ring.
  //  - If the node-id set is UNCHANGED (zero-gain expand, re-hydration,
  //    profile echo), the sim is not restarted and the camera not refit —
  //    the graph must not visibly react to a no-op.
  const prevPushRef = useRef<{ idsKey: string; model: GraphModel | null }>({
    idsKey: "",
    model: null,
  });
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !model.n) return;
    const idsKey = `${layout}|${model.indexToId.join(" ")}`;
    const prev = prevPushRef.current;
    const sameGraph = prev.idsKey === idsKey;
    if (prev.model && !sameGraph) {
      try {
        const live = graph.getPointPositions();
        if (live && live.length === prev.model.n * 2) {
          for (let i = 0; i < model.n; i++) {
            const pi = prev.model.idToIndex.get(model.indexToId[i]);
            if (pi !== undefined) {
              model.positions[i * 2] = live[pi * 2];
              model.positions[i * 2 + 1] = live[pi * 2 + 1];
            }
          }
        }
      } catch {
        /* keep seeded positions */
      }
    }
    prevPushRef.current = { idsKey, model };
    if (!sameGraph) graph.setPointPositions(model.positions);
    graph.setPointColors(colors);
    graph.setPointSizes(sizes);
    graph.setLinks(model.links);
    if (model.linkWidths.length) graph.setLinkWidths(model.linkWidths);
    if (model.linkColors.length) graph.setLinkColors(model.linkColors);
    if (model.linkArrows.length) graph.setLinkArrows(model.linkArrows);
    // Cosmos only tracks points that already exist — re-track the label set now
    // that positions are set, otherwise getTrackedPointPositionsMap() is empty
    // and no labels ever paint.
    overlayRef.current?.retrackLabels();
    const timers: number[] = [];
    if (sameGraph) {
      graph.render();
    } else if (layout === "circular") {
      // Keep the circular seed positions; don't run the force sim.
      graph.setPointPositions(model.positions, true);
      graph.render();
      graph.fitView(400);
      timers.push(
        window.setTimeout(() => overlayRef.current?.retrackLabels(), 450),
      );
    } else {
      graph.start();
      // Stage refits as the force layout expands so the graph never drifts
      // out of view before it settles. No hard pause timer — the tuned decay
      // cools the sim to a natural stop (onSimulationEnd does the final fit +
      // label repaint), which the user can re-energize via the play control.
      timers.push(window.setTimeout(() => graph.fitView(400), 300));
      timers.push(window.setTimeout(() => graph.fitView(400), 1200));
      timers.push(window.setTimeout(() => graph.fitView(400), 3000));
      timers.push(
        window.setTimeout(() => overlayRef.current?.retrackLabels(), 3100),
      );
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colors, model.n]);

  // Resize the seed marker on seed change without rebuilding the whole graph.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !model.n) return;
    graph.setPointSizes(sizes);
    graph.render();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
  }, [selectedNodeId, focusMode, model, graphRef]);

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

  return (
    <>
      <div ref={containerRef} className="ge-cosmos-canvas" />
      {!model.n ? (
        <div className="ge-placeholder">
          <span>{emptyHint}</span>
        </div>
      ) : null}
    </>
  );
}
