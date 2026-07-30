// Composition wrapper for the graph canvas: WebGL engine + label overlay +
// toolbar + legend. Owns the canvas-LOCAL ephemeral state (search, focus
// mode, hidden kinds, legend, label mode, sim state) — this state
// intentionally resets when the canvas remounts (e.g. on a mode switch or a
// fresh load). A caller may provide `stateKey` to retain these preferences
// across task unmounts for this browser session; they are never server-owned.

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Graph } from "@cosmos.gl/graph";
import type { GraphModel } from "../model/parseRows";
import { CanvasStats, type CanvasStatsData } from "./CanvasStats";
import { CanvasToolbar, type SimParams } from "./CanvasToolbar";
import {
  CosmosCanvas,
  type CanvasCameraSnapshot,
  type CanvasOverlayHandle,
  type CanvasSimControl,
} from "./CosmosCanvas";
import { GraphErrorBoundary } from "../GraphErrorBoundary";
import {
  GraphTableFallback,
  type GraphTableFallbackModel,
} from "../GraphTableFallback";
import { LabelsOverlay, type LabelMode } from "./LabelsOverlay";
import { Legend } from "./Legend";

/** Must mirror the CosmosCanvas config defaults — the Sim panel edits these
 * live via graph.setConfig. */
export const DEFAULT_SIM_PARAMS: SimParams = {
  repulsion: 2.4,
  linkDistance: 90,
  gravity: 0.12,
  friction: 0.88,
};

interface CanvasSessionState {
  search: string;
  focusMode: boolean;
  hiddenKinds: Set<string>;
  hiddenProfiles: Set<string>;
  legendOpen: boolean;
  statsOpen: boolean;
  userPaused: boolean;
  simParams: SimParams;
  labelMode: LabelMode;
  camera: CanvasCameraSnapshot | null;
}

/** Per-task, in-session canvas preferences. A stateKey opts in; callers that
 * omit it retain the historical remount-reset behavior. */
const CANVAS_SESSION_STATE = new Map<string, CanvasSessionState>();

export function resetCanvasSessionStateForTests(): void {
  CANVAS_SESSION_STATE.clear();
}

interface Props {
  model: GraphModel;
  selectedNodeId: string;
  /** Optional because legacy callers kept edge selection beside the canvas.
   * Supplying it lets the non-WebGL table preserve the same visual selection. */
  selectedEdgeId?: string;
  seedNodeId?: string;
  /** Rendered centre-stage when the model is empty. A ReactNode, not a string,
   * so a mode can offer a recovery ACTION (widen the window, clear a filter)
   * rather than only describing the emptiness. */
  emptyHint: ReactNode;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  /** Explicit expand (double-click). Depth is the caller's stepper value. */
  onExpandNode: (id: string) => void;
  /** Background click — callers typically clear the local selection. */
  onViewClick?: () => void;
  /** Optional stats chip rendered in the canvas top-right corner. */
  stats?: CanvasStatsData;
  /** Timeline: per-link/point visibility overrides (see CosmosCanvas). */
  linkOverride?: { alpha: Float32Array; width: Float32Array };
  pointAlphaOverride?: Float32Array;
  /** Stable task/mode key used to restore canvas-local controls after the view
   * unmounts (task switches). */
  stateKey?: string;
  /** Optional controlled relationship visibility. When supplied, this one
   * set drives link rendering and the legend; other modes keep local toggles. */
  visibleProfiles?: ReadonlySet<string>;
  onToggleProfileVisibility?: (profile: string, visible: boolean) => void;
  /** Keyboard/table fallback actions. Node action defaults to the existing
   * onExpandNode handler; labels are configurable per task. */
  fallbackNodeActionLabel?: string;
  onFallbackEdgeAction?: (edgeId: string) => void;
  fallbackEdgeActionLabel?: string;
  /** Hide the force Play/Pause. Default true — every force mode now shows it;
   * a static-layout mode (Flows-Layered) hides it via `staticLayout`. Also
   * hides the ⚙ Forces tuning panel: pausing and tuning are one feature, and
   * offering sliders with no way to stop the sim is a half-control. */
  showSimControls?: boolean;
  /**
   * Start with the kind/relationship legend collapsed. Default true (open),
   * which is right when this canvas owns the whole view. A host that already
   * shows the same swatches in its own toolbar should pass false: two legends
   * for one colour scale is noise, and the strip costs ~85px the graph wants.
   * The `Legend ▾` button still reveals it, and a user's own toggle is still
   * remembered per `stateKey`.
   */
  legendDefaultOpen?: boolean;
  /** Optional labels for the sim Play/Pause (Timeline uses "Layout" to
   * distinguish from its scrubber "Time" play). */
  simLabel?: { play: string; pause: string };
  /** Flows-Layered: precomputed authoritative layout; the force sim never runs
   * and the ⚙ Forces panel is hidden (meaningless without a sim). */
  staticLayout?: boolean;
  /** Extra overlays rendered inside the canvas wrap (e.g. the Timeline
   * scrubber strip — an overlay, so it costs no header height). */
  children?: ReactNode;
}

export function GraphCanvas({
  model,
  selectedNodeId,
  selectedEdgeId,
  seedNodeId,
  emptyHint,
  onSelectNode,
  onSelectEdge,
  onExpandNode,
  onViewClick,
  stats,
  linkOverride,
  pointAlphaOverride,
  stateKey,
  visibleProfiles,
  onToggleProfileVisibility,
  fallbackNodeActionLabel = "Investigate from here",
  onFallbackEdgeAction,
  fallbackEdgeActionLabel = "Open transactions",
  showSimControls = true,
  legendDefaultOpen = true,
  simLabel,
  staticLayout = false,
  children,
}: Props) {
  const graphRef = useRef<Graph | null>(null);
  const overlayRef = useRef<CanvasOverlayHandle | null>(null);
  const simControlRef = useRef<CanvasSimControl | null>(null);

  // A model object is the renderer's dataset revision: every graph build
  // publishes a new immutable model. Keep this counter local so a new dataset
  // retires an old renderer failure even when node/edge counts happen to match.
  const modelRevisionRef = useRef({ model, revision: 0 });
  if (modelRevisionRef.current.model !== model) {
    modelRevisionRef.current = {
      model,
      revision: modelRevisionRef.current.revision + 1,
    };
  }
  const [retryRevision, setRetryRevision] = useState(0);
  // Cosmos is an expensive WebGL allocation. Dataset revisions reset only the
  // error boundary; they must not remount a healthy renderer. Layout regime
  // changes and an explicit Retry still require a new constructor because
  // `enableSimulation` is create-time Cosmos configuration.
  const rendererMountKey = `${staticLayout ? "static" : "force"}:${retryRevision}`;
  const boundaryResetKey = `${rendererMountKey}:${modelRevisionRef.current.revision}`;
  const [rendererFailure, setRendererFailure] = useState<{
    resetKey: string;
    error: Error;
  } | null>(null);
  const activeRendererFailure =
    rendererFailure?.resetKey === boundaryResetKey
      ? rendererFailure.error
      : null;

  const fallbackModel = useMemo<GraphTableFallbackModel>(() => ({
    nodes: model.nodeRows.map((node) => ({
      id: node.id,
      label: node.label || node.id,
      kind: node.kind,
      summary: node.profiles.length
        ? `Relationships: ${node.profiles.join(", ")}`
        : undefined,
    })),
    edges: model.edgeRows
      .filter((edge) => !visibleProfiles || visibleProfiles.has(edge.profile))
      .map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.profile,
      weight: Number.isFinite(edge.weight) ? edge.weight : null,
      directed: edge.directed,
      summary: edge.edge_count > 1 ? `${edge.edge_count} records` : undefined,
      })),
  }), [model, visibleProfiles]);

  const activateFallback = (error: Error) => {
    setRendererFailure({ resetKey: boundaryResetKey, error });
  };

  const runRendererAction = (phase: string, action: () => void) => {
    try {
      action();
    } catch (caught) {
      const cause = caught instanceof Error ? caught : new Error(String(caught));
      activateFallback(new Error(`Cosmos renderer ${phase}: ${cause.message}`));
    }
  };

  const retryRenderer = () => {
    graphRef.current = null;
    overlayRef.current = null;
    simControlRef.current = null;
    setRendererFailure(null);
    setRetryRevision((revision) => revision + 1);
  };

  const fallback = (error: Error, retry?: () => void) => (
    <GraphTableFallback
      model={fallbackModel}
      error={error}
      title="Graph data table"
      emptyMessage={emptyHint}
      selectedNodeId={selectedNodeId}
      selectedEdgeId={selectedEdgeId}
      onRetry={() => {
        retry?.();
        retryRenderer();
      }}
      onSelectNode={onSelectNode}
      onSelectEdge={onSelectEdge}
      onNodeAction={onExpandNode}
      nodeActionLabel={fallbackNodeActionLabel}
      onEdgeAction={onFallbackEdgeAction}
      edgeActionLabel={fallbackEdgeActionLabel}
      style={{
        maxHeight: "none",
        overflow: "auto",
        borderRadius: 0,
        padding: "16px",
      }}
    />
  );

  // Canvas-local state. `stateKey` promotes it from mount-local to task-local
  // for this browser session; no server/reducer contract is involved.
  const cachedCanvasState = stateKey
    ? CANVAS_SESSION_STATE.get(stateKey)
    : undefined;
  const [search, setSearch] = useState(() => cachedCanvasState?.search ?? "");
  const [searchMiss, setSearchMiss] = useState(false);
  const [focusMode, setFocusMode] = useState(
    () => cachedCanvasState?.focusMode ?? false,
  );
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(
    () => new Set(cachedCanvasState?.hiddenKinds ?? []),
  );
  const [localHiddenProfiles, setLocalHiddenProfiles] = useState<Set<string>>(
    () => new Set(cachedCanvasState?.hiddenProfiles ?? []),
  );
  const [legendOpen, setLegendOpen] = useState(
    () => cachedCanvasState?.legendOpen ?? legendDefaultOpen,
  );
  // Stats chip is opt-out (compact by default, collapsible to a pill).
  const [statsOpen, setStatsOpen] = useState(
    () => cachedCanvasState?.statsOpen ?? true,
  );
  // Explicit user pause — drives both the freeze and the Play/Pause label.
  const [userPaused, setUserPaused] = useState(
    () => cachedCanvasState?.userPaused ?? false,
  );
  const [simParams, setSimParams] = useState<SimParams>(
    () => ({ ...(cachedCanvasState?.simParams ?? DEFAULT_SIM_PARAMS) }),
  );
  // Hover tooltip is the primary label affordance, so always-on labels default
  // to "off" — 100+ overlapping text pills hide the topology.
  const [labelMode, setLabelMode] = useState<LabelMode>(
    () => cachedCanvasState?.labelMode ?? "off",
  );
  const cameraRef = useRef<CanvasCameraSnapshot | null>(
    cachedCanvasState?.camera ?? null,
  );

  useEffect(() => {
    if (!stateKey) return;
    CANVAS_SESSION_STATE.set(stateKey, {
      search,
      focusMode,
      hiddenKinds: new Set(hiddenKinds),
      hiddenProfiles: new Set(localHiddenProfiles),
      legendOpen,
      statsOpen,
      userPaused,
      simParams: { ...simParams },
      labelMode,
      camera: cameraRef.current,
    });
  }, [
    stateKey,
    search,
    focusMode,
    hiddenKinds,
    localHiddenProfiles,
    legendOpen,
    statsOpen,
    userPaused,
    simParams,
    labelMode,
  ]);

  // Live sim tuning: push the changed parameter into the running engine and
  // re-energize so the layout re-settles under the new forces. A Forces change
  // is an explicit reheat — clear any pause and reheat through the shared path
  // (restores settle decay + regime so the burst can't cool under idle decay).
  const onSimParamsChange = (next: SimParams) => {
    setSimParams(next);
    const graph = graphRef.current;
    if (!graph) return;
    runRendererAction("force update failed", () => {
      graph.setConfig({
        simulationRepulsion: next.repulsion,
        simulationLinkDistance: next.linkDistance,
        simulationGravity: next.gravity,
        simulationFriction: next.friction,
      });
      setUserPaused(false);
      simControlRef.current?.reheat(0.3);
    });
  };

  // A static-layout flip (Flows Physics ⇄ Re-layer) remounts CosmosCanvas but
  // NOT GraphCanvas, so userPaused would carry over and start the new force
  // canvas already-paused. Reset it whenever the regime changes.
  const previousStaticLayoutRef = useRef(staticLayout);
  useEffect(() => {
    if (previousStaticLayoutRef.current !== staticLayout) {
      previousStaticLayoutRef.current = staticLayout;
      setUserPaused(false);
    }
  }, [staticLayout]);

  // Search → find by id/label substring, select + zoom.
  const runSearch = () => {
    const graph = graphRef.current;
    const q = search.trim().toLowerCase();
    if (!q) return;
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
      if (graph) {
        runRendererAction("search zoom failed", () =>
          graph.zoomToPointByIndex(hitIdx, 600, 4));
      }
    } else {
      setSearchMiss(true);
      window.setTimeout(() => setSearchMiss(false), 1800);
    }
  };

  // Play/pause the force simulation. Play re-energizes at PARTIAL alpha (0.4) —
  // a full-alpha restart on an already-spread layout re-inflated the cloud so
  // violently it left the viewport. The camera is DECOUPLED now (no tick
  // follow), so Play/Pause only start/stop the physics; the view holds where
  // the user left it.
  // Pause intent drives BOTH the freeze and the button label (cosmos's
  // isSimulationRunning / start-end callbacks are unreliable in this version,
  // so we don't depend on them). Not-paused ⇒ the keep-warm interval keeps the
  // layout breathing ⇒ show Pause. Paused ⇒ frozen ⇒ show Play.
  const toggleSim = () => {
    const graph = graphRef.current;
    if (!graph) return;
    runRendererAction("simulation control failed", () => {
      if (!userPaused) {
        setUserPaused(true);
        graph.pause();
      } else {
        setUserPaused(false);
        simControlRef.current?.reheat(0.4);
      }
    });
  };

  const toggleKind = (kind: string) => {
    setHiddenKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  };

  const hiddenProfiles = useMemo(() => {
    if (!visibleProfiles) return localHiddenProfiles;
    return new Set(
      [...model.profileColor.keys()].filter(
        (profile) => !visibleProfiles.has(profile),
      ),
    );
  }, [localHiddenProfiles, model.profileColor, visibleProfiles]);

  const toggleProfile = (profile: string) => {
    if (visibleProfiles) {
      onToggleProfileVisibility?.(profile, !visibleProfiles.has(profile));
      return;
    }
    setLocalHiddenProfiles((prev) => {
      const next = new Set(prev);
      if (next.has(profile)) next.delete(profile);
      else next.add(profile);
      return next;
    });
  };

  // Hiding an edge type zeroes that link's alpha AND width (width 0 also
  // suppresses clicks). Composed with any incoming override (Timeline's time
  // window) so the two seams cooperate instead of overwriting each other.
  const effectiveLinkOverride = useMemo(() => {
    if (!hiddenProfiles.size) return linkOverride;
    // Indexed by RENDERED link, not by edge row: parallel same-direction edges
    // are bundled into one link, so `edgeRows[i]` is no longer link `i` and
    // using it zeroed unrelated edges.
    const n = model.linkIds.length;
    const alpha = new Float32Array(n);
    const width = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const baseAlpha = linkOverride ? linkOverride.alpha[i] ?? 1 : 1;
      const baseWidth = linkOverride
        ? linkOverride.width[i] ?? model.linkWidths[i]
        : model.linkWidths[i];
      // A bundle disappears only when every profile inside it is hidden.
      const bundle = model.linkProfiles[i] ?? [];
      const hidden = bundle.length > 0 && bundle.every((p) => hiddenProfiles.has(p));
      alpha[i] = hidden ? 0 : baseAlpha;
      width[i] = hidden ? 0 : baseWidth;
    }
    return { alpha, width };
  }, [hiddenProfiles, linkOverride, model]);
  const rendererSelectedEdgeId =
    selectedEdgeId &&
    !hiddenProfiles.has(
      model.edgeRows.find((edge) => edge.id === selectedEdgeId)?.profile ?? "",
    )
      ? selectedEdgeId
      : undefined;

  return (
    <section className={`ge-graph-frame${children ? " ge-has-scrubber" : ""}`}>
      {/* The chrome is UNCONDITIONAL. It used to be gated on `model.n > 0`,
          which meant an empty graph removed the search box, Fit view, the
          force controls, the stats chip and the legend — i.e. every control
          that could explain or undo the emptiness disappeared at exactly the
          moment the user needed it. Controls that cannot act are disabled,
          not unmounted. */}
      <header className="ge-graph-chrome">
        <CanvasToolbar
          disabled={model.n === 0}
          search={search}
          searchMiss={searchMiss}
          onSearchChange={(v) => {
            setSearch(v);
            if (searchMiss) setSearchMiss(false);
          }}
          onRunSearch={runSearch}
          onFitView={() => {
            const g = graphRef.current;
            if (!g) return;
            runRendererAction("fit view failed", () => {
              g.setZoomLevel?.(1);
              g.fitView(500);
            });
          }}
          focusMode={focusMode}
          onToggleFocus={() => setFocusMode((v) => !v)}
          simRunning={!userPaused}
          onToggleSim={toggleSim}
          simControlLabel={simLabel}
          showSimControls={showSimControls && !staticLayout}
          showForcesPanel={showSimControls && !staticLayout}
          simParams={simParams}
          onSimParamsChange={onSimParamsChange}
          labelMode={labelMode}
          onLabelModeChange={setLabelMode}
        />
        <div className="ge-graph-chrome__end">
          {stats ? (
            <CanvasStats
              stats={stats}
              open={statsOpen}
              onToggleOpen={() => setStatsOpen((v) => !v)}
            />
          ) : null}
          <button
            type="button"
            className={`ge-graph-btn ${legendOpen ? "active" : ""}`}
            aria-expanded={legendOpen}
            disabled={model.n === 0}
            onClick={() => setLegendOpen((value) => !value)}
          >
            Legend {legendOpen ? "▾" : "▸"}
          </button>
        </div>
      </header>
      {legendOpen && model.n > 0 ? (
        <Legend
          model={model}
          hiddenKinds={hiddenKinds}
          onToggleKind={toggleKind}
          hiddenProfiles={hiddenProfiles}
          onToggleProfile={toggleProfile}
          open
          onToggleOpen={() => setLegendOpen(false)}
          showToggle={false}
        />
      ) : null}
      <div className="ge-graph-stage">
      {activeRendererFailure ? fallback(activeRendererFailure) : (
        <GraphErrorBoundary
          resetKey={boundaryResetKey}
          fallback={(error, retry) => fallback(error, retry)}
        >
          <CosmosCanvas
            // Remount when the layout regime flips or an explicit retry is
            // requested: enableSimulation and WebGL setup are create-time.
            key={rendererMountKey}
            model={model}
            selectedNodeId={selectedNodeId}
            selectedEdgeId={rendererSelectedEdgeId}
            seedNodeId={seedNodeId}
            focusMode={focusMode}
            hiddenKinds={hiddenKinds}
            graphRef={graphRef}
            overlayRef={overlayRef}
            simControlRef={simControlRef}
            emptyHint={emptyHint}
            onSelectNode={onSelectNode}
            onSelectEdge={onSelectEdge}
            onExpandNode={onExpandNode}
            onViewClick={onViewClick}
            staticLayout={staticLayout}
            userPaused={userPaused}
            linkOverride={effectiveLinkOverride}
            pointAlphaOverride={pointAlphaOverride}
            initialCamera={cameraRef.current}
            onCameraStateChange={(camera) => {
              cameraRef.current = camera;
              if (!stateKey) return;
              const current = CANVAS_SESSION_STATE.get(stateKey);
              if (current) {
                CANVAS_SESSION_STATE.set(stateKey, { ...current, camera });
              }
            }}
            onRendererError={activateFallback}
          />
          <LabelsOverlay
            model={model}
            seedNodeId={seedNodeId}
            selectedNodeId={selectedNodeId}
            labelMode={labelMode}
            graphRef={graphRef}
            overlayRef={overlayRef}
          />
        </GraphErrorBoundary>
      )}
      {children}
      </div>
    </section>
  );
}
