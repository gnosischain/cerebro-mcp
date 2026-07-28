// A measured, zoomable, pannable viewport for hand-rolled SVG surfaces.
//
// Replaces the fixed `viewBox` that made the Money Trail hop map unreadable:
// the drawing was laid out at a hardcoded 1040 user-unit width with a height
// that grew to ~1400, then squeezed into whatever the pane happened to be, so
// a wide short pane letterboxed it into illegibility with no way to zoom.
//
// Here the pane is MEASURED and handed to the layout, and the camera is an
// explicit, persisted piece of state.
//
// Two render slots:
//   children        world space   — inside the transformed <g>, scales with zoom
//   chrome(camera)  screen space  — outside it, so axis labels and coverage
//                                   banners keep a constant size
//
// Interaction notes that are easy to get wrong:
//   * The wheel listener is registered manually with `{ passive: false }`.
//     React attaches `onWheel` at the root as PASSIVE, so `preventDefault()`
//     there is a no-op and the page scrolls instead of the map zooming.
//   * macOS trackpad pinch arrives as a `wheel` event with `ctrlKey` set.
//   * Drag uses pointer capture, and a drag beyond a few px swallows the
//     subsequent `click` so panning never selects a ribbon by accident.

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { SvgViewportControls } from "./SvgViewportControls";
import {
  clampCamera,
  fitAll,
  fitWidth,
  isSameCamera,
  panBy,
  wheelZoomFactor,
  zoomAt,
  type Box,
  type Camera,
  type Size,
} from "./svgCamera";
import { readCamera, writeCamera } from "./svgCameraSession";

/** Used before the pane has been measured (SSR, and the first paint in jsdom
 * where ResizeObserver may be absent). Matches the historical layout width so
 * a non-measuring environment renders what it always did. */
const FALLBACK_SIZE: Size = { width: 1040, height: 620 };

/** Movement beyond this many px counts as a pan, not a click. */
const DRAG_SLOP_PX = 4;

interface Props {
  /** World-space bounding box of the drawing. */
  contentBox: Box;
  /** Identifies the surface for session camera memory. */
  stateKey?: string;
  /** Changes only when the underlying QUESTION changes; a new universe re-fits. */
  universeKey?: string;
  /** Default camera. "width" keeps a tall map legible and scrolls it. */
  fitMode?: "width" | "all";
  padding?: number;
  className?: string;
  ariaLabel: string;
  /** World-space content. Receives the live camera so a child can compensate
   * for scale where it must (e.g. keeping hit targets a constant size). */
  children: (camera: Camera, size: Size) => ReactNode;
  /** Screen-space overlay rendered inside the <svg>, above the content. */
  chrome?: (camera: Camera, size: Size) => ReactNode;
  onBackgroundClick?: () => void;
  /** Extra controls rendered beside the zoom buttons. */
  controlsSlot?: ReactNode;
  /** Measured pane size. Lets the caller lay out to the REAL width instead of
   * a hardcoded one — the defect this component exists to fix. */
  onMeasure?: (size: Size) => void;
}

export function SvgViewport({
  contentBox,
  stateKey = "",
  universeKey = "",
  fitMode = "width",
  padding = 20,
  className = "",
  ariaLabel,
  children,
  chrome,
  onBackgroundClick,
  controlsSlot,
  onMeasure,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState<Size>(FALLBACK_SIZE);

  // ---- Measure the pane -------------------------------------------------
  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const apply = () => {
      const rect = host.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        setSize((prev) =>
          Math.abs(prev.width - rect.width) < 1 &&
          Math.abs(prev.height - rect.height) < 1
            ? prev
            : { width: rect.width, height: rect.height },
        );
      }
    };
    apply();
    // Belt and braces. ResizeObserver is the precise signal (it catches pane
    // changes that are not window changes, e.g. dragging the table/map
    // splitter), but it is not universally delivered — it does not fire at all
    // under CDP viewport emulation, and jsdom may not define it. A window
    // resize listener costs nothing and covers the common case on its own.
    window.addEventListener("resize", apply);
    if (typeof ResizeObserver === "undefined") {
      return () => window.removeEventListener("resize", apply);
    }
    const observer = new ResizeObserver(apply);
    observer.observe(host);
    return () => {
      window.removeEventListener("resize", apply);
      observer.disconnect();
    };
  }, []);

  const measureRef = useRef(onMeasure);
  measureRef.current = onMeasure;
  useEffect(() => {
    measureRef.current?.(size);
  }, [size]);

  const defaultCamera = useMemo(
    () =>
      fitMode === "all"
        ? fitAll(contentBox, size, padding)
        : fitWidth(contentBox, size, padding),
    [contentBox, size, padding, fitMode],
  );

  const [camera, setCameraState] = useState<Camera>(
    () => readCamera(stateKey, universeKey) ?? defaultCamera,
  );

  // Track whether the analyst has taken manual control. Until they do, the
  // view keeps re-fitting as the pane is measured and the layout settles.
  //
  // This matters more than it looks: measurement, the caller's re-layout at
  // the measured width, and the fit resolve across THREE renders. Fitting
  // once on "first measure" locked the camera to an intermediate content
  // width (a 1040px fallback pane) and left the map at 72% in a pane it
  // should have filled. Once the analyst zooms or pans, their camera wins.
  const lastUniverse = useRef<string>(universeKey);
  const userAdjusted = useRef(false);
  useEffect(() => {
    if (lastUniverse.current !== universeKey) {
      lastUniverse.current = universeKey;
      userAdjusted.current = false;
      setCameraState(readCamera(stateKey, universeKey) ?? defaultCamera);
      return;
    }
    if (userAdjusted.current) return;
    setCameraState((prev) => (isSameCamera(prev, defaultCamera) ? prev : defaultCamera));
  }, [universeKey, stateKey, defaultCamera]);

  const setCamera = useCallback(
    (next: Camera) => {
      userAdjusted.current = true;
      const clamped = clampCamera(next, contentBox, size);
      setCameraState(clamped);
      writeCamera(stateKey, universeKey, clamped);
    },
    [contentBox, size, stateKey, universeKey],
  );

  // ---- Wheel / pinch ----------------------------------------------------
  const cameraRef = useRef(camera);
  cameraRef.current = camera;
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = svg.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      // ctrlKey => macOS pinch; it arrives finer-grained, so amplify it.
      const factor = wheelZoomFactor(event.ctrlKey ? event.deltaY * 2 : event.deltaY);
      setCamera(zoomAt(cameraRef.current, x, y, factor));
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [setCamera]);

  // ---- Drag to pan ------------------------------------------------------
  const dragRef = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const swallowClick = useRef(false);

  const onPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0 && event.button !== 1) return;
    dragRef.current = { x: event.clientX, y: event.clientY, moved: false };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const onPointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < DRAG_SLOP_PX) return;
    drag.moved = true;
    drag.x = event.clientX;
    drag.y = event.clientY;
    setCamera(panBy(cameraRef.current, dx, dy));
  };
  const endDrag = (event: React.PointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    dragRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    if (drag?.moved) swallowClick.current = true;
  };

  const atDefault = isSameCamera(camera, defaultCamera);

  return (
    <div
      ref={hostRef}
      className={`ge-svgvp${className ? ` ${className}` : ""}${
        dragRef.current ? " is-panning" : ""
      }`}
    >
      <div className="ge-svgvp__bar">
        <SvgViewportControls
          scale={camera.scale}
          onZoomIn={() =>
            setCamera(zoomAt(camera, size.width / 2, size.height / 2, 1.25))
          }
          onZoomOut={() =>
            setCamera(zoomAt(camera, size.width / 2, size.height / 2, 0.8))
          }
          onFitWidth={() => setCamera(fitWidth(contentBox, size, padding))}
          onFitAll={() => setCamera(fitAll(contentBox, size, padding))}
          atDefault={atDefault}
          onReset={() => setCamera(defaultCamera)}
        />
        {controlsSlot}
      </div>
      <svg
        ref={svgRef}
        className="ge-svgvp__svg"
        role="group"
        aria-label={ariaLabel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onClickCapture={(event) => {
          // A pan must never be read as a click on whatever was underneath.
          if (swallowClick.current) {
            swallowClick.current = false;
            event.stopPropagation();
            return;
          }
          if (event.target === event.currentTarget) onBackgroundClick?.();
        }}
      >
        <g transform={`translate(${camera.tx} ${camera.ty}) scale(${camera.scale})`}>
          {children(camera, size)}
        </g>
        {chrome?.(camera, size)}
      </svg>
    </div>
  );
}
