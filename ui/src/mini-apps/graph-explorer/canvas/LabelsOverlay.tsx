// HTML label overlay + hover tooltip for the WebGL canvas (moved from the
// old CosmosGraph). Cosmos GL is a GPU point/line renderer with no native
// text — we position absolutely-placed HTML labels for the most
// interpretable nodes (seed, selected, selected's neighbors, top-degree
// hubs) and reposition them on every simulation tick / zoom via tracked
// screen positions. The graph engine calls back into this overlay through
// the shared `overlayRef` handle.

import { useEffect, useRef, type MutableRefObject } from "react";
import type { Graph } from "@cosmos.gl/graph";
import type { GraphModel } from "../model/parseRows";
import type { CanvasOverlayHandle } from "./CosmosCanvas";

export type LabelMode = "all" | "auto" | "off";

const LABEL_ALL_CAP = 1500;

interface Props {
  model: GraphModel;
  seedNodeId?: string;
  selectedNodeId: string;
  labelMode: LabelMode;
  graphRef: MutableRefObject<Graph | null>;
  overlayRef: MutableRefObject<CanvasOverlayHandle | null>;
}

export function LabelsOverlay({
  model,
  seedNodeId,
  selectedNodeId,
  labelMode,
  graphRef,
  overlayRef,
}: Props) {
  const labelLayerRef = useRef<HTMLDivElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const labelIndicesRef = useRef<number[]>([]);
  const modelRef = useRef(model);
  useEffect(() => {
    modelRef.current = model;
  }, [model]);

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
  // tracks points that already exist), so the engine's data-push effect calls
  // this through the overlay handle too.
  const retrackLabels = () => {
    const graph = graphRef.current;
    if (!graph) return;
    graph.trackPointPositionsByIndices(labelIndicesRef.current);
    updateLabels();
  };

  const showTooltip = (index: number, pointPosition: [number, number]) => {
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
  };

  const hideTooltip = () => {
    tooltipRef.current?.classList.remove("visible");
  };

  // Register the imperative handle for the graph engine (tick/zoom/data-push
  // callbacks). Refreshed every render so the closures stay live.
  useEffect(() => {
    overlayRef.current = { updateLabels, retrackLabels, showTooltip, hideTooltip };
  });
  useEffect(() => {
    return () => {
      overlayRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Decide which nodes get labels. Modes:
  //   off  → none
  //   all  → every node (hard-capped on very large graphs; falls back to the
  //          curated subset above the cap to stay legible/performant)
  //   auto → curated subset: seed + selection + its neighbors + top-degree hubs
  useEffect(() => {
    if (!model.n) {
      labelIndicesRef.current = [];
      updateLabels();
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

  return (
    <>
      <div ref={labelLayerRef} className="ge-label-layer" aria-hidden />
      <div ref={tooltipRef} className="ge-node-tooltip" aria-hidden />
    </>
  );
}
