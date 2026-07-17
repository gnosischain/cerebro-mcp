// Composition wrapper for the graph canvas: WebGL engine + label overlay +
// toolbar + legend. Owns the canvas-LOCAL ephemeral state (search, focus
// mode, hidden kinds, legend, label mode, sim state) — this state
// intentionally resets when the canvas remounts (e.g. on a mode switch or a
// fresh load); it is never persisted server-side.

import { useRef, useState } from "react";
import type { Graph } from "@cosmos.gl/graph";
import type { GraphModel } from "../model/parseRows";
import type { GraphLayout } from "../types";
import { CanvasToolbar } from "./CanvasToolbar";
import { CosmosCanvas, type CanvasOverlayHandle } from "./CosmosCanvas";
import { LabelsOverlay, type LabelMode } from "./LabelsOverlay";
import { Legend } from "./Legend";

interface Props {
  model: GraphModel;
  selectedNodeId: string;
  seedNodeId?: string;
  layout: GraphLayout;
  emptyHint: string;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  /** Explicit expand (double-click). Depth is the caller's stepper value. */
  onExpandNode: (id: string) => void;
  /** Background click — callers typically clear the local selection. */
  onViewClick?: () => void;
}

export function GraphCanvas({
  model,
  selectedNodeId,
  seedNodeId,
  layout,
  emptyHint,
  onSelectNode,
  onSelectEdge,
  onExpandNode,
  onViewClick,
}: Props) {
  const graphRef = useRef<Graph | null>(null);
  const overlayRef = useRef<CanvasOverlayHandle | null>(null);

  // Canvas-local ephemeral state.
  const [search, setSearch] = useState("");
  const [searchMiss, setSearchMiss] = useState(false);
  const [focusMode, setFocusMode] = useState(false);
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(new Set());
  const [legendOpen, setLegendOpen] = useState(true);
  const [simRunning, setSimRunning] = useState(false);
  // Hover tooltip is the primary label affordance, so always-on labels default
  // to "off" — 100+ overlapping text pills hide the topology.
  const [labelMode, setLabelMode] = useState<LabelMode>("off");

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

  // Play/pause the force simulation. Play re-energizes at PARTIAL alpha —
  // start() with full alpha 1 on an already-spread layout re-inflated the
  // cloud so violently it left the viewport ("the graph disappears"). The
  // camera keeps up via the tick-follow in CosmosCanvas (throttled fitView
  // on every sim beat); pause refits once so a mid-flight halt never
  // strands the graph off-screen.
  const toggleSim = () => {
    const graph = graphRef.current;
    if (!graph) return;
    if (graph.isSimulationRunning) {
      graph.pause();
      graph.fitView(300);
    } else {
      graph.start(0.4);
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

  return (
    <div className="ge-cosmos-wrap">
      <CosmosCanvas
        model={model}
        selectedNodeId={selectedNodeId}
        seedNodeId={seedNodeId}
        focusMode={focusMode}
        layout={layout}
        hiddenKinds={hiddenKinds}
        graphRef={graphRef}
        overlayRef={overlayRef}
        emptyHint={emptyHint}
        onSelectNode={onSelectNode}
        onSelectEdge={onSelectEdge}
        onExpandNode={onExpandNode}
        onViewClick={onViewClick}
        onSimRunningChange={setSimRunning}
      />
      <LabelsOverlay
        model={model}
        seedNodeId={seedNodeId}
        selectedNodeId={selectedNodeId}
        labelMode={labelMode}
        graphRef={graphRef}
        overlayRef={overlayRef}
      />
      {model.n > 0 ? (
        <>
          <CanvasToolbar
            search={search}
            searchMiss={searchMiss}
            onSearchChange={(v) => {
              setSearch(v);
              if (searchMiss) setSearchMiss(false);
            }}
            onRunSearch={runSearch}
            onFit={() => graphRef.current?.fitView(500)}
            onRecenter={() => {
              const g = graphRef.current;
              if (!g) return;
              g.setZoomLevel?.(1);
              g.fitView(500);
            }}
            focusMode={focusMode}
            onToggleFocus={() => setFocusMode((v) => !v)}
            simRunning={simRunning}
            onToggleSim={toggleSim}
            labelMode={labelMode}
            onLabelModeChange={setLabelMode}
          />
          <Legend
            model={model}
            hiddenKinds={hiddenKinds}
            onToggleKind={toggleKind}
            open={legendOpen}
            onToggleOpen={() => setLegendOpen((v) => !v)}
          />
        </>
      ) : null}
    </div>
  );
}
