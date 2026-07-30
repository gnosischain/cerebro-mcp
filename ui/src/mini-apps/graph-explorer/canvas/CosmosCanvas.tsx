// The pure WebGL renderer (extracted from the old CosmosGraph — the engine
// code is MOVED, not rewritten). It owns the @cosmos.gl/graph instance and
// nothing else: labels/tooltips live in LabelsOverlay (reached through
// `overlayRef`), controls in CanvasToolbar, composition in GraphCanvas.

import { Graph } from "@cosmos.gl/graph";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
  type ReactNode,
} from "react";
import { SPACE_SIZE, type GraphModel } from "../model/parseRows";
import { COLOR_BY_KIND, FALLBACK_COLOR, SEED_COLOR, hexToRgba } from "../model/palette";

/** Imperative surface LabelsOverlay registers so the graph callbacks (tick /
 * zoom / data push) can drive label + tooltip painting without re-creating
 * the Graph. */
export interface CanvasOverlayHandle {
  updateLabels: () => void;
  retrackLabels: () => void;
  showTooltip: (index: number, pointPosition: [number, number]) => void;
  hideTooltip: () => void;
}

/** Imperative sim control CosmosCanvas registers so GraphCanvas's Play /
 * Forces controls reheat through the SAME path (settle decay + regime reset),
 * never leaving a hot burst to cool under the slow idle decay. */
export interface CanvasSimControl {
  reheat: (alpha: number) => void;
}

/** Browser-session camera state. Positions are keyed by node id rather than
 * array index so a task can be restored safely after rows are reordered (or
 * after a small incremental expansion). */
export interface CanvasCameraSnapshot {
  zoom: number;
  center: [number, number];
  nodePositions: ReadonlyMap<string, [number, number]>;
}

type CameraGraph = Pick<
  Graph,
  | "fitViewByPointPositions"
  | "getPointPositions"
  | "getZoomLevel"
  | "screenToSpacePosition"
  | "setZoomLevel"
>;

const finitePoint = (point: [number, number]): boolean =>
  Number.isFinite(point[0]) && Number.isFinite(point[1]);

export function captureCanvasCamera(
  graph: CameraGraph,
  model: Pick<GraphModel, "indexToId" | "n">,
  viewport: { width: number; height: number },
): CanvasCameraSnapshot | null {
  if (viewport.width < 2 || viewport.height < 2 || model.n === 0) return null;
  const zoom = graph.getZoomLevel();
  const center = graph.screenToSpacePosition([
    viewport.width / 2,
    viewport.height / 2,
  ]);
  const positions = graph.getPointPositions();
  if (
    !Number.isFinite(zoom) ||
    zoom <= 0 ||
    !finitePoint(center) ||
    positions.length !== model.n * 2
  ) {
    return null;
  }
  const nodePositions = new Map<string, [number, number]>();
  for (let i = 0; i < model.n; i++) {
    const id = model.indexToId[i];
    const point: [number, number] = [positions[i * 2], positions[i * 2 + 1]];
    if (id && finitePoint(point)) nodePositions.set(id, point);
  }
  if (!nodePositions.size) return null;
  return { zoom, center, nodePositions };
}

export function positionsFromCameraSnapshot(
  model: Pick<GraphModel, "indexToId" | "positions">,
  snapshot: CanvasCameraSnapshot,
): Float32Array {
  const positions = Float32Array.from(model.positions);
  for (let i = 0; i < model.indexToId.length; i++) {
    const saved = snapshot.nodePositions.get(model.indexToId[i]);
    if (!saved || !finitePoint(saved)) continue;
    positions[i * 2] = saved[0];
    positions[i * 2 + 1] = saved[1];
  }
  return positions;
}

export function cameraSnapshotMatchesModel(
  snapshot: CanvasCameraSnapshot | null | undefined,
  model: Pick<GraphModel, "indexToId">,
): snapshot is CanvasCameraSnapshot {
  return Boolean(
    snapshot &&
    Number.isFinite(snapshot.zoom) &&
    snapshot.zoom > 0 &&
    finitePoint(snapshot.center) &&
    model.indexToId.some((id) => snapshot.nodePositions.has(id)),
  );
}

/** Restore with public Cosmos APIs only. `fitViewByPointPositions` centers the
 * saved world coordinate; once its zero-duration d3 transition has committed,
 * `setZoomLevel` applies the saved scale around that same viewport center.
 * Two animation frames avoid racing d3's deferred transition without relying
 * on Cosmos's private transform method. */
export function restoreCanvasCamera(
  graph: Pick<CameraGraph, "fitViewByPointPositions" | "setZoomLevel">,
  snapshot: CanvasCameraSnapshot,
  onComplete?: () => void,
): () => void {
  let cancelled = false;
  let secondFrame = 0;
  graph.fitViewByPointPositions([...snapshot.center], 0);
  const firstFrame = window.requestAnimationFrame(() => {
    secondFrame = window.requestAnimationFrame(() => {
      if (cancelled) return;
      graph.setZoomLevel(snapshot.zoom, 0);
      onComplete?.();
    });
  });
  return () => {
    cancelled = true;
    window.cancelAnimationFrame(firstFrame);
    if (secondFrame) window.cancelAnimationFrame(secondFrame);
  };
}

interface Props {
  model: GraphModel;
  selectedNodeId: string;
  selectedEdgeId?: string;
  seedNodeId?: string;
  focusMode: boolean;
  hiddenKinds: Set<string>;
  /** Owned by GraphCanvas; shared with the toolbar (fit/recenter/search). */
  graphRef: MutableRefObject<Graph | null>;
  overlayRef: MutableRefObject<CanvasOverlayHandle | null>;
  /** Optional: GraphCanvas registers here to reheat the sim (Play / Forces). */
  simControlRef?: MutableRefObject<CanvasSimControl | null>;
  emptyHint: ReactNode;
  onSelectNode: (id: string) => void;
  onSelectEdge: (id: string) => void;
  onExpandNode: (id: string) => void;
  /** Background (non-node, non-edge) click. */
  onViewClick?: () => void;
  onSimRunningChange?: (running: boolean) => void;
  /** Flows seam: the layout is precomputed and authoritative — pushes use
   * setPointPositions(positions, true) + render(); the force sim NEVER runs
   * (no start(), no keep-warm). */
  staticLayout?: boolean;
  /** True while the user has explicitly paused the sim (keep-warm respects
   * it — the simmer must not resurrect a paused layout). */
  userPaused?: boolean;
  /** Timeline seam: per-link alpha/width override composed onto the model's
   * link colors each frame — applied via setLinkColors/setLinkWidths +
   * render(), which does NOT touch simulation state. Alpha 0 hides; width 0
   * also suppresses clicks on hidden links. */
  linkOverride?: { alpha: Float32Array; width: Float32Array };
  /** Timeline seam: per-point alpha multiplier (dims nodes with no visible
   * incident edge in the window). */
  pointAlphaOverride?: Float32Array;
  /** Optional task-local camera captured by GraphCanvas. */
  initialCamera?: CanvasCameraSnapshot | null;
  onCameraStateChange?: (camera: CanvasCameraSnapshot) => void;
  /** Renderer failures happen predominantly in effects and Cosmos callbacks,
   * which React error boundaries cannot catch. Report them to GraphCanvas so
   * it can retire only this renderer and keep the investigative shell alive. */
  onRendererError?: (error: Error) => void;
}

/** Cooling length (ticks) for the force sim — a smooth settle in ~10-15s. */
export const SETTLE_DECAY = 1000;
/** After a data change the camera fits ONCE, when the layout has settled below
 * this alpha — so the graph frames itself without the camera moving while it
 * spreads (the "always recentering" the user hated), and without leaving a big
 * result off-screen. */
export const FIRST_FIT_GATE = 0.1;
/** Cap the zoom after an auto-fit so a 1–2 node result doesn't blow up into a
 * giant blurry dot. */
export const MAX_AUTO_ZOOM = 3.5;

/** Link colors + widths to push to Cosmos for the current override state.
 *
 * ABSENCE of an override is itself a state to apply — it means "show every
 * link at its baseline". The effect used to `return` early when the override
 * was undefined, so un-hiding the last edge type (which drops the override
 * back to undefined outside Timeline) left the zeroed widths and alphas on the
 * GPU: edges could be toggled OFF but never back ON. Pure so vitest can pin
 * the round trip without a WebGL context.
 */
export function composeLinkVisuals(
  model: Pick<
    GraphModel,
    "linkColors" | "linkWidths" | "linkIds" | "linkEdgeIds"
  >,
  linkOverride?: { alpha: Float32Array; width: Float32Array },
  selectedEdgeId?: string,
  edgeToLinkIndex?: ReadonlyMap<string, number>,
): { colors: Float32Array; widths: Float32Array } {
  const selectedLinkIndex = selectedEdgeId
    ? edgeToLinkIndex?.get(selectedEdgeId) ??
      model.linkEdgeIds.findIndex((edgeIds) => edgeIds.includes(selectedEdgeId))
    : -1;
  if (!linkOverride && selectedLinkIndex < 0) {
    return { colors: model.linkColors, widths: model.linkWidths };
  }
  const linkCount = model.linkIds.length;
  const colors = new Float32Array(model.linkColors);
  const widths = linkOverride
    ? new Float32Array(linkOverride.width)
    : new Float32Array(model.linkWidths);
  for (let i = 0; i < linkCount; i++) {
    const alpha = linkOverride?.alpha[i] ?? 1;
    colors[i * 4 + 3] = model.linkColors[i * 4 + 3] * alpha;
    // Selection is a visual overlay, never a visibility override. A link
    // hidden by profile/time filtering (zero alpha OR width) must stay hidden.
    if (
      i === selectedLinkIndex &&
      colors[i * 4 + 3] > 0 &&
      (widths[i] ?? 0) > 0
    ) {
      colors[i * 4] = 1;
      colors[i * 4 + 1] = 0.72;
      colors[i * 4 + 2] = 0.12;
      colors[i * 4 + 3] = 1;
      widths[i] = Math.max(widths[i] * 1.8, widths[i] + 2);
    }
  }
  return { colors, widths };
}

export function sameNodeUniverse(
  previous: readonly string[],
  next: readonly string[],
): boolean {
  if (previous.length !== next.length) return false;
  for (let index = 0; index < next.length; index++) {
    if (previous[index] !== next[index]) return false;
  }
  return true;
}

/**
 * Index of the candidate whose rendered centre is closest to the click, or -1.
 *
 * A pick square catches every point that overlaps it, and in a dense cluster
 * that is several nodes. Returning the first hit selects by buffer order, which
 * from the user's side looks random; nearest-centre selects what they aimed at.
 */
export function nearestPointToClick(
  candidates: number[],
  screenOf: (index: number) => [number, number],
  click: [number, number],
): number {
  let nearest = -1;
  let best = Infinity;
  for (const index of candidates) {
    const [sx, sy] = screenOf(index);
    const dist = Math.hypot(sx - click[0], sy - click[1]);
    if (dist < best) {
      best = dist;
      nearest = index;
    }
  }
  return nearest;
}

/**
 * True when the last push targeted THIS graph instance with THIS node universe
 * — i.e. the positions already in the renderer can be reused.
 *
 * The instance check is load-bearing. `sameNodeUniverse` alone answers "is the
 * data the same", and the push effect uses that to SKIP `setPointPositions`.
 * But the memo of the last push lives in a ref, which survives the renderer
 * being torn down and rebuilt — so after any remount with unchanged data
 * (StrictMode's double-mount, a `staticLayout` flip, "Retry visual renderer")
 * the effect concluded "already pushed" and handed the FRESH graph no point
 * buffers at all. The next `render()` then died inside cosmos with
 * `(regl) missing buffer for attribute "pointIndices"`, which surfaces as
 * "Visual renderer unavailable" and the table fallback.
 *
 * It went unnoticed in the Graph Explorer because its model is empty on mount
 * (data arrives from a later tool call, after the double-mount) so the ref was
 * still blank. A mini-app that mounts with rows already in hand — governance's
 * GIP graph reads synchronous descriptor previews — hit it every single time.
 */
export function samePushTarget(
  previous: { ids: readonly string[]; graph: unknown },
  graph: unknown,
  nextIds: readonly string[],
): boolean {
  if (previous.graph !== graph) return false;
  return sameNodeUniverse(previous.ids, nextIds);
}

/** Pure reheat-alpha (vitest target): a no-op republish gets 0; a fresh/
 * replaced graph settles fully; an incremental push (positions retained) gets
 * a gentle top-up. */
export function reEnergizeAlpha(opts: {
  sameGraph: boolean;
  retained: number;
  prevN: number;
  nextN: number;
}): number {
  if (opts.sameGraph) return 0;
  const churn = opts.prevN
    ? 1 - opts.retained / Math.max(opts.nextN, opts.prevN)
    : 1;
  return churn > 0.3 ? 1 : 0.3;
}

export function detectWebGL(): boolean {
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
  selectedEdgeId,
  seedNodeId,
  focusMode,
  hiddenKinds,
  graphRef,
  overlayRef,
  simControlRef,
  emptyHint,
  onSelectNode,
  onSelectEdge,
  onExpandNode,
  onViewClick,
  onSimRunningChange,
  staticLayout = false,
  userPaused = false,
  linkOverride,
  pointAlphaOverride,
  initialCamera,
  onCameraStateChange,
  onRendererError,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [webglOk] = useState(detectWebGL);
  const rendererFailedRef = useRef(false);
  const onRendererErrorRef = useRef(onRendererError);
  onRendererErrorRef.current = onRendererError;
  const onCameraStateChangeRef = useRef(onCameraStateChange);
  onCameraStateChangeRef.current = onCameraStateChange;
  const initialCameraRef = useRef(initialCamera);
  const cameraRestoredRef = useRef(false);
  const cameraRestorePendingRef = useRef(false);
  const cancelCameraRestoreRef = useRef<(() => void) | null>(null);

  const reportRendererError = (caught: unknown, phase: string) => {
    if (rendererFailedRef.current) return;
    rendererFailedRef.current = true;
    const cause = caught instanceof Error ? caught : new Error(String(caught));
    const error = new Error(`Cosmos renderer ${phase}: ${cause.message}`);
    onRendererErrorRef.current?.(error);
  };

  /** Cosmos invokes these callbacks outside React. A thrown callback would
   * otherwise become an uncaught window error instead of activating the
   * first-class table surface. */
  const guardRuntime = <TArgs extends unknown[]>(
    phase: string,
    callback: (...args: TArgs) => void,
  ) => (...args: TArgs) => {
    try {
      callback(...args);
    } catch (error) {
      reportRendererError(error, phase);
    }
  };
  /**
   * A point click SELECTS. Expand is a separate `dblclick` path (see the canvas
   * listeners below) and never inferred from click timing.
   *
   * It used to be inferred: "a second click on the same index within 320ms means
   * expand". That is unsafe here because this stack dispatches the SAME physical
   * click twice — verified in the browser: two `click` events, identical
   * offsets, identical timeStamp. Any duplicate or fast repeat then reads as a
   * double-click, so a single click could expand a node and replace the panel
   * the user was trying to read. The DOM already reports genuine double-clicks;
   * inferring them from clicks we know arrive doubled cannot be made reliable.
   */
  const routePointClick = (index: number) => {
    const id = modelRef.current.indexToId[index];
    if (!id) return;
    cbRef.current.onSelectNode(id);
  };

  /** Expand on a real double-click. Separate from selection so a duplicate
   * `click` dispatch can never be mistaken for one. */
  const routePointExpand = (index: number) => {
    const id = modelRef.current.indexToId[index];
    if (id) cbRef.current.onExpandNode(id);
  };
  /**
   * Timestamp of the last click a point-pick claimed, so `onBackgroundClick`
   * can stand down. Cosmos fires its background callback whenever ITS pick
   * misses — which, given the pick is what's broken, is every node click.
   * Without this the CPU fallback selects a node and cosmos immediately clears
   * it in the same gesture.
   */
  const pointClaimedClickRef = useRef(0);
  // Camera lock: false while framing a fresh graph's initial spread; true once
  // locked (expands/Play/resize never recenter). A full-churn push unlocks it.
  // Set true on every data change; the tick performs ONE fit when the layout
  // settles below FIRST_FIT_GATE, then clears it. No continuous follow.
  const pendingFitRef = useRef(false);
  // Last observed canvas size — a pending fit waits for two identical
  // readings so it never lands mid grid-transition.
  const lastSizeRef = useRef({ w: -1, h: -1 });
  // Live mirrors for the create-once callbacks (they capture creation scope).
  const keepWarmStateRef = useRef({ staticLayout, userPaused, hasNodes: false });
  keepWarmStateRef.current.staticLayout = staticLayout;
  keepWarmStateRef.current.userPaused = userPaused;

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
  const baseColors = useMemo(() => {
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

  const pointColorBuffersRef = useRef<[Float32Array, Float32Array]>([
    new Float32Array(0),
    new Float32Array(0),
  ]);
  const pointColorBufferIndexRef = useRef(0);
  const colors = useMemo(() => {
    if (!pointAlphaOverride) return baseColors;
    let buffers = pointColorBuffersRef.current;
    if (buffers[0].length !== baseColors.length) {
      buffers = [
        new Float32Array(baseColors.length),
        new Float32Array(baseColors.length),
      ];
      pointColorBuffersRef.current = buffers;
      pointColorBufferIndexRef.current = 0;
    }
    pointColorBufferIndexRef.current = 1 - pointColorBufferIndexRef.current;
    const out = buffers[pointColorBufferIndexRef.current];
    out.set(baseColors);
    const count = Math.min(model.n, pointAlphaOverride.length);
    for (let index = 0; index < count; index++) {
      out[index * 4 + 3] *= pointAlphaOverride[index];
    }
    return out;
  }, [baseColors, model.n, pointAlphaOverride]);

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
    if (!webglOk) {
      reportRendererError(
        new Error("WebGL is unavailable or disabled in this browser"),
        "capability check failed",
      );
    }
    // The callback is intentionally reached through a ref; this effect is a
    // one-time capability gate rather than a response to parent renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [webglOk]);

  useEffect(() => {
    if (!webglOk || !containerRef.current) return;
    const container = containerRef.current;
    let graph: Graph;
    try {
      graph = new Graph(container, {
      backgroundColor: [0, 0, 0, 0],
      spaceSize: SPACE_SIZE,
      // Flows: the layered layout is authoritative and the physics engine must
      // be OFF entirely — not merely "not restarted". With the sim enabled the
      // graph auto-runs an initial cycle on first data set, which drifted the
      // layered positions and read as perpetual "hiccups". enableSimulation
      // false makes render() never advance physics (points stay exactly where
      // setPointPositions puts them). GraphCanvas remounts this component when
      // staticLayout flips (keyed), so the flag is always correct at creation.
      enableSimulation: !staticLayout,
      pointSize: 3,
      pointSizeScale: 1,
      linkColor: [0.55, 0.6, 0.7, 0.6],
      linkWidth: 1,
      linkWidthScale: 1,
      // Curved links so RECIPROCAL edges stop hiding each other. The shader
      // places the control point at `(a+b)/2 + normal * dist * h`, and the
      // normal is derived from (b - a) — so A→B and B→A bow to OPPOSITE sides
      // and both become visible. Straight lines drew them exactly on top of
      // each other, which read as a single one-way transfer.
      //
      // LIMIT worth knowing: `h` is a GLOBAL scalar, so two SAME-direction
      // edges between the same pair still coincide exactly. No cosmos config
      // fixes that (there is no per-link curvature in 2.6.4) — parallel
      // same-direction edges are merged with a multiplicity count instead
      // (see collapseParallelLinks in parseRows).
      curvedLinks: true,
      curvedLinkSegments: 16,
      // Gentler than the 0.5 default: a deep bow pushes long edges far from
      // the straight path and makes the topology harder to read.
      curvedLinkControlPointDistance: 0.3,
      curvedLinkWeight: 0.8,
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
      // The camera is decoupled from the sim: the ONLY automatic fit is the
      // first non-empty data push (owned in the push effect via
      // shouldFitOnPush). Everything else — ticks, cycle-ends, resize, expand
      // — leaves the user's zoom/pan untouched; Fit/Recenter/Search are the
      // sanctioned camera moves.
      fitViewOnInit: false,
      hoveredPointCursor: "pointer",
      // Focus mode relies on selection greyout: non-selected points/links dim.
      pointGreyoutOpacity: 0.12,
      linkGreyoutOpacity: 0.06,
      // Spread-tuned defaults (user-adjustable live via the Sim panel —
      // GraphCanvas pushes overrides through graph.setConfig). Stronger
      // repulsion + longer link distance open dense hub-spoke clusters into a
      // readable cloud inside the 8192 space; moderate gravity keeps the
      // cloud centered so the camera-follow never has to chase it far. Decay
      // is the cooling length in ticks: 1200 settles in roughly 15-20s.
      simulationFriction: 0.88,
      simulationGravity: 0.12,
      simulationCenter: 0,
      simulationRepulsion: 2.4,
      simulationRepulsionTheta: 1.15,
      simulationLinkSpring: 0.35,
      simulationLinkDistance: 90,
      simulationDecay: SETTLE_DECAY,
      // The tick NEVER follows the sim. It paints labels and, after a data
      // change (pendingFit), performs exactly ONE fit once the layout has
      // settled below the gate — so the graph frames itself when it's done
      // moving, and the camera never chases the spread.
      onSimulationTick: guardRuntime("simulation tick failed", (alpha?: number) => {
        overlayRef.current?.updateLabels();
        const a = typeof alpha === "number" ? alpha : 0;
        if (pendingFitRef.current && a < FIRST_FIT_GATE) {
          pendingFitRef.current = false;
          frameGraph();
        }
      }),
      onSimulationStart: guardRuntime("simulation start callback failed", () =>
        cbRef.current.onSimRunningChange?.(true)),
      onSimulationPause: guardRuntime("simulation pause callback failed", () =>
        cbRef.current.onSimRunningChange?.(false)),
      onSimulationUnpause: guardRuntime("simulation resume callback failed", () =>
        cbRef.current.onSimRunningChange?.(true)),
      // CONTINUOUS LAYOUT: a cycle ending re-injects KEEP_WARM_ALPHA so the
      // layout keeps gently breathing forever (the camera is decoupled, so
      // this never recenters). Never for static layouts (Flows), the user's
      // pause, or an empty canvas.
      onSimulationEnd: guardRuntime("simulation end callback failed", () =>
        cbRef.current.onSimRunningChange?.(false)),
      onZoom: guardRuntime("zoom callback failed", () =>
        overlayRef.current?.updateLabels()),
      onBackgroundClick: guardRuntime("background selection failed", () => {
        // A point pick (native or the CPU fallback) already claimed this click.
        if (Date.now() - pointClaimedClickRef.current < 250) return;
        cbRef.current.onViewClick?.();
      }),
      onPointClick: guardRuntime("node selection failed", (index: number) => {
        pointClaimedClickRef.current = Date.now();
        routePointClick(index);
      }),
      // Cosmos has no double-click callback; expand is wired to a real
      // `dblclick` on the canvas below.
      onLinkClick: guardRuntime("edge selection failed", (linkIndex: number) => {
        const id = modelRef.current.linkIds[linkIndex];
        if (id) cbRef.current.onSelectEdge(id);
      }),
      // Hover tooltip — the primary way to read a node's address/label without
      // covering the whole graph in always-on text. The overlay derives the
      // screen position from the point's space coordinate so it tracks the
      // node precisely regardless of the (variably-typed) DOM/D3 event.
      onPointMouseOver: guardRuntime("node hover failed", (index: number, pointPosition: [number, number]) => {
        overlayRef.current?.showTooltip(index, pointPosition);
      }),
      onPointMouseOut: guardRuntime("node hover cleanup failed", () => {
        overlayRef.current?.hideTooltip();
      }),
      });
    } catch (error) {
      reportRendererError(error, "initialization failed");
      return;
    }
    graphRef.current = graph;

    // CPU PICK FALLBACK -- restores click-to-select.
    //
    // Cosmos resolves a clicked point on the GPU: `findHoveredItem()` runs a
    // pick pass into `points.hoveredFbo` and reads back an index + a found
    // flag. On this stack that flag never comes back set, so `onPointClick`
    // never fires and `onBackgroundClick` fires for every node click instead --
    // i.e. clicking a node silently CLEARED the selection. Verified in the
    // browser: with the cursor 1px from a node's centre, cosmos had the mouse
    // mapped correctly (`store.mousePosition` matched the point's space
    // position to ~1 unit) yet `store.hoveredPoint` stayed undefined. Not a
    // size, pause, DPR, overlay or pointer-delivery problem -- all eliminated.
    //
    // `getPointsInRect`, cosmos's own CPU-side spatial query, resolves the same
    // point correctly from the raw event offset. So we pick there and route
    // through the same handler. Cosmos's native `onPointClick` is left wired:
    // whichever path resolves first claims the click, so if the GPU pick starts
    // working (library fix, other hardware) this becomes dead weight rather
    // than a double-selection.
    const PICK_RADIUS_PX = 14;

    /** Which point is under this pointer event, or -1. */
    const pickAt = (event: MouseEvent): number => {
      const g = graphRef.current;
      if (!g || !modelRef.current.n) return -1;
      const x = event.offsetX;
      const y = event.offsetY;
      if (!Number.isFinite(x) || !Number.isFinite(y)) return -1;
      const hits = g.getPointsInRect([
        [x - PICK_RADIUS_PX, y - PICK_RADIUS_PX],
        [x + PICK_RADIUS_PX, y + PICK_RADIUS_PX],
      ]);
      if (!hits || hits.length === 0) return -1;
      // Several points can share the pick square; take the nearest centre so a
      // click between two nodes resolves the way the user aimed. Positions are
      // fetched ONCE — `getPointPositions()` copies the whole buffer out of the
      // GPU, so calling it per candidate turned a click into O(hits) copies.
      const positions = g.getPointPositions();
      return nearestPointToClick(
        Array.from(hits),
        (index) => g.spaceToScreenPosition([positions[index * 2], positions[index * 2 + 1]]),
        [x, y],
      );
    };

    // This stack dispatches the same physical click TWICE (verified: identical
    // offsets AND identical timeStamp), so dedupe on the event's own timeStamp
    // rather than a wall-clock window — a duplicate shares it, a genuine second
    // click cannot.
    let lastHandledStamp = -1;

    const onCanvasClick = guardRuntime("fallback node pick failed", (event: Event) => {
      const mouse = event as MouseEvent;
      if (mouse.timeStamp === lastHandledStamp) return;
      // Already handled by cosmos's own pick for this gesture.
      if (Date.now() - pointClaimedClickRef.current < 250) return;
      const nearest = pickAt(mouse);
      if (nearest < 0) return;
      lastHandledStamp = mouse.timeStamp;
      pointClaimedClickRef.current = Date.now();
      routePointClick(nearest);
    });

    const onCanvasDoubleClick = guardRuntime("node expand failed", (event: Event) => {
      const mouse = event as MouseEvent;
      const nearest = pickAt(mouse);
      if (nearest < 0) return;
      // Suppress the trailing click of the double-click gesture so the pair
      // does not also re-select.
      pointClaimedClickRef.current = Date.now();
      routePointExpand(nearest);
    });

    // Capture phase: cosmos's own click handling runs on the canvas too, and we
    // must set the claim flag before its background callback can read it.
    container.addEventListener("click", onCanvasClick, true);
    container.addEventListener("dblclick", onCanvasDoubleClick, true);

    // Context loss is dispatched by the canvas rather than thrown through
    // React. Capture it at the renderer container so it follows the same
    // isolated fallback path as initialization and buffer-update failures.
    const onContextLost = (event: Event) => {
      event.preventDefault();
      reportRendererError(new Error("WebGL context was lost"), "runtime failed");
    };
    container.addEventListener("webglcontextlost", onContextLost, true);
    return () => {
      container.removeEventListener("click", onCanvasClick, true);
      container.removeEventListener("dblclick", onCanvasDoubleClick, true);
      container.removeEventListener("webglcontextlost", onContextLost, true);
      const restoreWasPending = cameraRestorePendingRef.current;
      cancelCameraRestoreRef.current?.();
      cancelCameraRestoreRef.current = null;
      cameraRestorePendingRef.current = false;
      try {
        // If unmount happens during the two-frame public-API restore, retain
        // the known-good incoming snapshot instead of capturing its temporary
        // centering zoom. Otherwise capture the exact live layout/camera just
        // before Cosmos releases its buffers.
        const camera = restoreWasPending
          ? initialCameraRef.current ?? null
          : captureCanvasCamera(graph, modelRef.current, {
              width: container.clientWidth,
              height: container.clientHeight,
            });
        if (camera) onCameraStateChangeRef.current?.(camera);
      } catch {
        // Camera persistence is best-effort during teardown. A context-loss
        // failure must not mask the original renderer error.
      }
      try {
        graph.destroy();
      } catch {
        // The renderer is already being retired. A failed teardown must not
        // replace the original, actionable failure shown in the fallback.
      }
      graphRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [webglOk]);

  // Keep the WebGL canvas sized to its container. Cosmos sizes to the canvas
  // but only re-measures on an explicit render() — it has no internal
  // ResizeObserver — so toggling the details panel (the .ge-body grid flips
  // 1fr 320px ↔ 1fr 0) or resizing the desktop window left the canvas at its
  // old width and clipped on the right. Re-measure via render() only —
  // deliberately NO fitView: a panel toggle must not recenter the graph
  // (content stays anchored in world space; the user's zoom/pan is preserved).
  useEffect(() => {
    const wrap = containerRef.current?.parentElement;
    if (!wrap || typeof ResizeObserver === "undefined") return;
    let raf = 0;
    const ro = new ResizeObserver(() => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        try {
          const graph = graphRef.current;
          if (!graph) return;
          graph.render();
          overlayRef.current?.updateLabels();
          // No-op unless a fit is PENDING — an ordinary panel toggle or window
          // resize must never recenter a graph the user has already positioned.
          attemptFit();
        } catch (error) {
          reportRendererError(error, "resize render failed");
        }
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
  // Fit the whole graph into view, then cap the zoom so a 1–2 node result
  // doesn't become a giant blurry dot. The single sanctioned auto-frame.
  /** Request a fit. It lands only once the canvas has a STABLE, non-zero size.
   *
   * The container is not its final size when data arrives: `.ge-body` animates
   * `grid-template-columns` for 250ms when the details panel opens/closes, and
   * a freshly mounted mode can measure 0×0 for a frame. Fitting against that
   * box frames a canvas that no longer exists a moment later, which is why the
   * graph kept landing off-screen and only a manual Fit rescued it.
   *
   * `attemptFit` is also called from the ResizeObserver, but it is a NO-OP
   * unless a fit is pending — so an ordinary panel toggle or window resize
   * still never recenters the view.
   */
  const frameGraph = () => {
    if (!graphRef.current) return;
    pendingFitRef.current = true;
    lastSizeRef.current = { w: -1, h: -1 };
    attemptFit();
    // Fallback: a container that never fires another resize would otherwise
    // hold the fit forever.
    window.setTimeout(() => {
      try {
        if (pendingFitRef.current) {
          lastSizeRef.current = canvasSize();
          attemptFit();
        }
      } catch (error) {
        reportRendererError(error, "deferred frame failed");
      }
    }, 400);
  };

  const canvasSize = () => {
    const wrap = containerRef.current?.parentElement;
    return { w: wrap?.clientWidth ?? 0, h: wrap?.clientHeight ?? 0 };
  };

  const attemptFit = () => {
    if (!pendingFitRef.current || !graphRef.current) return;
    const { w, h } = canvasSize();
    if (w < 2 || h < 2) return; // not laid out yet — wait for the next resize
    const last = lastSizeRef.current;
    lastSizeRef.current = { w, h };
    if (last.w !== w || last.h !== h) return; // still animating — wait
    pendingFitRef.current = false;
    doFit();
  };

  const doFit = () => {
    // Defer a beat before fitting: with enableSimulation:false cosmos rescales
    // and re-measures AFTER the push, so an immediate fitView frames a stale
    // bounding box and leaves the graph as a tiny speck (manual Fit then
    // worked, which is what proved the timing was the bug).
    window.setTimeout(() => {
      try {
        const g = graphRef.current;
        if (!g) return;
        g.fitView(400);
        // Only the DEGENERATE case (a lone node / single pair) needs a zoom cap —
        // fitView on one point zooms to fill and renders a giant blurry dot.
        // Clamping any larger graph would shrink a legitimately tight layout.
        if (modelRef.current.n > 2) return;
        window.setTimeout(() => {
          try {
            const g2 = graphRef.current;
            if (g2 && (g2.getZoomLevel?.() ?? 1) > MAX_AUTO_ZOOM) {
              g2.setZoomLevel?.(MAX_AUTO_ZOOM, 200);
            }
          } catch (error) {
            reportRendererError(error, "zoom cap failed");
          }
        }, 430);
      } catch (error) {
        reportRendererError(error, "fit view failed");
      }
    }, 150);
  };

  // Reheat = the ONE path that injects settle energy. Registered on
  // simControlRef so GraphCanvas's Play / Forces reheat identically.
  const reheat = (alpha: number) => {
    try {
      const graph = graphRef.current;
      if (!graph) return;
      graph.setConfig({ simulationDecay: SETTLE_DECAY });
      graph.start(alpha);
    } catch (error) {
      reportRendererError(error, "simulation restart failed");
    }
  };
  useEffect(() => {
    if (simControlRef) simControlRef.current = { reheat };
    return () => {
      if (simControlRef) simControlRef.current = null;
    };
  });

  const restoreInitialCameraOnce = (
    graph: Graph,
    currentModel: GraphModel,
  ): boolean => {
    if (cameraRestoredRef.current) return false;
    cameraRestoredRef.current = true;
    const snapshot = initialCameraRef.current;
    if (!cameraSnapshotMatchesModel(snapshot, currentModel)) return false;
    pendingFitRef.current = false;
    cameraRestorePendingRef.current = true;
    cancelCameraRestoreRef.current?.();
    cancelCameraRestoreRef.current = restoreCanvasCamera(
      graph,
      snapshot,
      () => {
        cameraRestorePendingRef.current = false;
        cancelCameraRestoreRef.current = null;
        overlayRef.current?.updateLabels();
      },
    );
    return true;
  };

  // `graph` is part of the memo, not just the data: see `samePushTarget`.
  const prevPushRef = useRef<{
    ids: readonly string[];
    model: GraphModel | null;
    graph: unknown;
  }>({ ids: [], model: null, graph: null });
  useEffect(() => {
    const graph = graphRef.current;
    keepWarmStateRef.current.hasNodes = model.n > 0;
    if (!graph || !model.n) return;
    try {
      const cachedCamera =
        !cameraRestoredRef.current &&
        cameraSnapshotMatchesModel(initialCameraRef.current, model)
          ? initialCameraRef.current
          : null;
      if (staticLayout) {
      // Flows: the precomputed layered layout is AUTHORITATIVE — push it
      // outright (no live-position retention), render, and fit ONCE (the first
      // non-empty push per mount). The force sim never runs here.
      graph.setPointPositions(model.positions, true);
      graph.setPointColors(colors);
      graph.setPointSizes(sizes);
      graph.setLinks(model.links);
      if (model.linkWidths.length) graph.setLinkWidths(model.linkWidths);
      if (model.linkColors.length) graph.setLinkColors(model.linkColors);
      if (model.linkArrows.length) graph.setLinkArrows(model.linkArrows);
      overlayRef.current?.retrackLabels();
      graph.render();
      // Deterministic layout, no sim. A returning task restores its camera;
      // first load and later trace/merge results still receive the normal fit.
      if (!restoreInitialCameraOnce(graph, model)) frameGraph();
        return;
      }
      const prev = prevPushRef.current;
      const sameGraph = samePushTarget(prev, graph, model.indexToId);
    // Topology churn measured on RETAINED ids (not count delta — a same-sized
    // but completely different graph must read as full churn). Drives the
    // re-energize alpha below.
      let retained = 0;
      if (prev.model && !sameGraph) {
      try {
        const live = graph.getPointPositions();
        const havePrev = live && live.length === prev.model.n * 2;
        for (let i = 0; i < model.n; i++) {
          const pi = prev.model.idToIndex.get(model.indexToId[i]);
          if (pi !== undefined) {
            retained++;
            if (havePrev) {
              model.positions[i * 2] = live[pi * 2];
              model.positions[i * 2 + 1] = live[pi * 2 + 1];
            }
          }
        }
      } catch {
        /* keep seeded positions */
      }
      }
      prevPushRef.current = { ids: model.indexToId, model, graph };
      if (!sameGraph) {
        graph.setPointPositions(
          cachedCamera
            ? positionsFromCameraSnapshot(model, cachedCamera)
            : model.positions,
        );
      }
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
      const alpha = reEnergizeAlpha({
        sameGraph,
        retained,
        prevN: prev.model?.n ?? 0,
        nextN: model.n,
      });
      // Materialize buffers before applying the saved world-center transform.
      // `render()` does not alter simulation state.
      let restoredCamera = false;
      if (cachedCamera) {
        graph.render();
        restoredCamera = restoreInitialCameraOnce(graph, model);
      } else if (!cameraRestoredRef.current) {
        // The saved camera belongs to a wholly different node universe. Retire
        // it so a later incremental response cannot unexpectedly resurrect it.
        cameraRestoredRef.current = true;
      }
      if (sameGraph) {
        graph.render();
      } else if (keepWarmStateRef.current.userPaused) {
      // TRUE FREEZE: paused means paused. Buffers are set + rendered above, but
      // we must not start()/reheat. Still frame the new result once (a paused
      // expand should show its result, just without motion).
      graph.render();
      if (!restoredCamera) {
        pendingFitRef.current = true;
        timers.push(window.setTimeout(
          guardRuntime("paused graph frame failed", () => frameGraph()),
          200,
        ));
      }
      } else {
      // A data change (new seed / mode / expand / merge): the tick will fit
      // ONCE when this settles below the gate — framing the result without
      // chasing the spread.
      pendingFitRef.current = !restoredCamera;
      reheat(alpha);
      timers.push(
        window.setTimeout(
          guardRuntime("deferred label tracking failed", () =>
            overlayRef.current?.retrackLabels()),
          3100,
        ),
      );
      // Safety net: if the sim settles so fast the tick's gate never catches a
      // pending fit (or ticks stop early), frame it after the settle window.
      timers.push(
        window.setTimeout(guardRuntime("settled graph frame failed", () => {
          if (pendingFitRef.current) {
            pendingFitRef.current = false;
            frameGraph();
          }
        }), 2600),
      );
      }
      return () => timers.forEach((t) => window.clearTimeout(t));
    } catch (error) {
      reportRendererError(error, "data update failed");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model, staticLayout]);

  const edgeToLinkIndex = useMemo(() => {
    const index = new Map<string, number>();
    model.linkEdgeIds.forEach((edgeIds, linkIndex) => {
      edgeIds.forEach((edgeId) => index.set(edgeId, linkIndex));
    });
    return index;
  }, [model.linkEdgeIds]);

  const linkVisuals = useMemo(
    () => composeLinkVisuals(
      model,
      linkOverride,
      selectedEdgeId,
      edgeToLinkIndex,
    ),
    [edgeToLinkIndex, linkOverride, model, selectedEdgeId],
  );

  // Recolor on seed/hidden-kind change without rebuilding the whole graph.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !model.n) return;
    try {
      graph.setPointColors(colors);
      graph.render();
    } catch (error) {
      reportRendererError(error, "node color update failed");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colors, model.n]);

  // Timeline frame: compose the override alpha into the model's link colors
  // and rewrite the width buffer, then render(). Deliberately NO start(), NO
  // refit — cosmos applies buffer rewrites without touching sim state, so
  // scrubbing/playback never perturbs the settled layout.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !model.n) return;
    try {
      const { colors, widths } = linkVisuals;
      graph.setLinkColors(colors);
      graph.setLinkWidths(widths);
      graph.render();
    } catch (error) {
      reportRendererError(error, "link visibility update failed");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkVisuals, model.n]);

  // Resize the seed marker on seed change without rebuilding the whole graph.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !model.n) return;
    try {
      graph.setPointSizes(sizes);
      graph.render();
    } catch (error) {
      reportRendererError(error, "node size update failed");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sizes, model.n]);

  // Reflect external selection + focus mode into Cosmos.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    try {
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
    } catch (error) {
      reportRendererError(error, "selection update failed");
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
          <div className="ge-placeholder__body">{emptyHint}</div>
        </div>
      ) : null}
    </>
  );
}
