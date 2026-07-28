// Per-surface, in-session camera memory for SVG viewports.
//
// Mirrors CANVAS_SESSION_STATE in GraphCanvas.tsx: a module-scoped Map plus a
// test reset, so a task switch (or any remount) does not throw away where the
// analyst had scrolled to.
//
// Keyed by (stateKey, universeKey). The universeKey deliberately is NOT the
// scope_id: expanding one node with "Trace out" mints a NEW scope_id while the
// drawing is substantially the same, and that is precisely the moment the
// camera must hold still so the analyst can see what the expansion added.
// The universe changes only when the QUESTION changes — different seeds, a
// different window, direction, USD floor or token filter.

import type { Camera } from "./svgCamera";

const CAMERA_SESSION = new Map<string, Camera>();

export function resetSvgCameraSessionForTests(): void {
  CAMERA_SESSION.clear();
}

function key(stateKey: string, universeKey: string): string {
  return `${stateKey}::${universeKey}`;
}

export function readCamera(
  stateKey: string,
  universeKey: string,
): Camera | null {
  if (!stateKey) return null;
  return CAMERA_SESSION.get(key(stateKey, universeKey)) ?? null;
}

export function writeCamera(
  stateKey: string,
  universeKey: string,
  camera: Camera,
): void {
  if (!stateKey) return;
  CAMERA_SESSION.set(key(stateKey, universeKey), camera);
}

export function clearCamera(stateKey: string, universeKey: string): void {
  CAMERA_SESSION.delete(key(stateKey, universeKey));
}
