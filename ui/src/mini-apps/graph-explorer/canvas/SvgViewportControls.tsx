// Zoom controls for an SVG viewport. Deliberately small — the size of React
// Flow's `<Controls>` in the Model Lineage app, not the CanvasToolbar (which
// carries force-simulation tuning that a static layout has no use for).
//
// No slider: dataZoom-style slider bars are banned on every chart surface in
// this repo (wheel/pinch + buttons only).

interface Props {
  scale: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitWidth: () => void;
  onFitAll: () => void;
  /** True when the camera is already at the default fit. */
  atDefault: boolean;
  onReset: () => void;
}

export function SvgViewportControls({
  scale,
  onZoomIn,
  onZoomOut,
  onFitWidth,
  onFitAll,
  atDefault,
  onReset,
}: Props) {
  return (
    <div className="ge-svgvp__controls" role="group" aria-label="Zoom controls">
      <button type="button" className="ge-graph-btn" onClick={onZoomOut} title="Zoom out">
        −
      </button>
      <span className="ge-svgvp__zoom" aria-live="off" title="Current zoom">
        {Math.round(scale * 100)}%
      </span>
      <button type="button" className="ge-graph-btn" onClick={onZoomIn} title="Zoom in">
        +
      </button>
      <button
        type="button"
        className="ge-graph-btn"
        onClick={onFitWidth}
        title="Fit the columns to the pane width; scroll for a long tail"
      >
        Fit width
      </button>
      <button
        type="button"
        className="ge-graph-btn"
        onClick={onFitAll}
        title="Fit the entire map in view"
      >
        Fit all
      </button>
      <button
        type="button"
        className="ge-graph-btn"
        onClick={onReset}
        disabled={atDefault}
        title="Back to the default view"
      >
        Reset
      </button>
    </div>
  );
}
