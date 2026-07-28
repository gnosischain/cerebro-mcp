// Pure 2-D camera for hand-rolled SVG surfaces (Money Trail hop map, and the
// time-sankey that follows). No DOM, no React — every function here is a plain
// transform so the interaction layer stays thin and the maths stays testable.
//
// Model: a uniform scale plus a translation, applied as the SVG transform
// `translate(tx, ty) scale(scale)`. Screen = world * scale + t.
//
// Why this exists: MoneySankey rendered into a FIXED `viewBox` whose width was
// permanently 1040 user-units (`buildMoneySankeyLayout` defaults `options.width`
// and MoneySankey never passed one), while its height grew with the number of
// counterparties — up to ~1400 at 40 per hop. A tall, narrow drawing squeezed
// into a wide, short box letterboxes: the whole map shrinks until nothing is
// legible, and there was no zoom, pan or fit control anywhere to recover.

export interface Camera {
  scale: number;
  tx: number;
  ty: number;
}

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Size {
  width: number;
  height: number;
}

export const MIN_SCALE = 0.05;
export const MAX_SCALE = 12;

/** Keep at least this many screen px of content inside the viewport when
 * panning, so the drawing can never be flung completely out of sight. */
const KEEP_VISIBLE_PX = 48;

export const IDENTITY: Camera = { scale: 1, tx: 0, ty: 0 };

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

export function clampScale(scale: number): number {
  if (!Number.isFinite(scale) || scale <= 0) return 1;
  return clamp(scale, MIN_SCALE, MAX_SCALE);
}

/** World point -> screen point. */
export function project(
  camera: Camera,
  x: number,
  y: number,
): { x: number; y: number } {
  return { x: x * camera.scale + camera.tx, y: y * camera.scale + camera.ty };
}

/** Screen point -> world point. */
export function unproject(
  camera: Camera,
  x: number,
  y: number,
): { x: number; y: number } {
  return { x: (x - camera.tx) / camera.scale, y: (y - camera.ty) / camera.scale };
}

/**
 * Scale so the whole content box fits inside the viewport, then centre it.
 *
 * This is the operation the old fixed `viewBox` was doing implicitly and
 * badly: it always fit BOTH axes, so a 1040x1400 drawing in a 1300x600 pane
 * shrank to 43% and the labels became unreadable. Here it is an explicit,
 * re-runnable action ("Fit all") rather than the only thing that can happen.
 */
export function fitAll(content: Box, size: Size, padding = 24): Camera {
  if (
    !(content.width > 0) ||
    !(content.height > 0) ||
    !(size.width > 0) ||
    !(size.height > 0)
  ) {
    return { ...IDENTITY };
  }
  const usableW = Math.max(1, size.width - padding * 2);
  const usableH = Math.max(1, size.height - padding * 2);
  const scale = clampScale(
    Math.min(usableW / content.width, usableH / content.height),
  );
  return centerOn(content, size, scale);
}

/**
 * Fit the WIDTH only and pin to the top.
 *
 * The natural reading of a hop map: columns span the pane, and a long tail of
 * counterparties scrolls vertically instead of being shrunk into illegibility.
 * Never magnifies past 1:1 — a two-node trail should not be blown up.
 */
export function fitWidth(content: Box, size: Size, padding = 24): Camera {
  if (!(content.width > 0) || !(size.width > 0)) return { ...IDENTITY };
  const usableW = Math.max(1, size.width - padding * 2);
  const scale = clampScale(Math.min(1, usableW / content.width));
  const tx = padding - content.x * scale;
  // Centre vertically when the content is shorter than the pane; otherwise pin
  // the top so the seed column starts where the eye already is.
  const scaledH = content.height * scale;
  const ty =
    scaledH < size.height
      ? (size.height - scaledH) / 2 - content.y * scale
      : padding - content.y * scale;
  return { scale, tx, ty };
}

export function centerOn(content: Box, size: Size, scale: number): Camera {
  const s = clampScale(scale);
  return {
    scale: s,
    tx: (size.width - content.width * s) / 2 - content.x * s,
    ty: (size.height - content.height * s) / 2 - content.y * s,
  };
}

/**
 * Zoom about a fixed screen point (the cursor), so the thing under the pointer
 * stays under the pointer. Anything else feels like the map is running away.
 */
export function zoomAt(
  camera: Camera,
  screenX: number,
  screenY: number,
  factor: number,
): Camera {
  const scale = clampScale(camera.scale * factor);
  if (scale === camera.scale) return camera;
  const world = unproject(camera, screenX, screenY);
  return {
    scale,
    tx: screenX - world.x * scale,
    ty: screenY - world.y * scale,
  };
}

export function panBy(camera: Camera, dx: number, dy: number): Camera {
  return { scale: camera.scale, tx: camera.tx + dx, ty: camera.ty + dy };
}

/**
 * Keep a sliver of the drawing on screen. Without this, one hard drag or a
 * fast wheel at the edge leaves an empty pane and no obvious way back other
 * than hunting for Reset.
 */
export function clampCamera(camera: Camera, content: Box, size: Size): Camera {
  if (!(content.width > 0) || !(content.height > 0)) return camera;
  const left = content.x * camera.scale;
  const right = (content.x + content.width) * camera.scale;
  const top = content.y * camera.scale;
  const bottom = (content.y + content.height) * camera.scale;
  const keepX = Math.min(KEEP_VISIBLE_PX, right - left);
  const keepY = Math.min(KEEP_VISIBLE_PX, bottom - top);
  return {
    scale: camera.scale,
    tx: clamp(camera.tx, keepX - right, size.width - left - keepX),
    ty: clamp(camera.ty, keepY - bottom, size.height - top - keepY),
  };
}

/** True when the camera is (near enough) the one `fit` would produce. */
export function isSameCamera(a: Camera, b: Camera, epsilon = 0.5): boolean {
  return (
    Math.abs(a.scale - b.scale) < epsilon / 100 &&
    Math.abs(a.tx - b.tx) < epsilon &&
    Math.abs(a.ty - b.ty) < epsilon
  );
}

/** Wheel delta -> zoom factor. Trackpads emit many small deltas and mice a few
 * large ones; the exponential keeps both feeling proportional. */
export function wheelZoomFactor(deltaY: number): number {
  return Math.exp(-clamp(deltaY, -120, 120) / 320);
}
