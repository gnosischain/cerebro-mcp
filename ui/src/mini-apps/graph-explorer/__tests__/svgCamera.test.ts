import { describe, expect, it } from "vitest";
import {
  MAX_SCALE,
  MIN_SCALE,
  clampCamera,
  clampScale,
  fitAll,
  fitWidth,
  panBy,
  project,
  unproject,
  wheelZoomFactor,
  zoomAt,
} from "../canvas/svgCamera";

const CONTENT = { x: 0, y: 0, width: 1040, height: 1400 };
const PANE = { width: 1300, height: 600 };

describe("project / unproject", () => {
  it("round-trips a point", () => {
    const camera = { scale: 2.5, tx: -130, ty: 44 };
    const screen = project(camera, 321, 654);
    const world = unproject(camera, screen.x, screen.y);
    expect(world.x).toBeCloseTo(321, 6);
    expect(world.y).toBeCloseTo(654, 6);
  });
});

describe("fitAll", () => {
  it("fits both axes and centres the content", () => {
    const camera = fitAll(CONTENT, PANE, 0);
    // Height is the binding axis: 600/1400 < 1300/1040.
    expect(camera.scale).toBeCloseTo(600 / 1400, 6);
    const topLeft = project(camera, 0, 0);
    const bottomRight = project(camera, CONTENT.width, CONTENT.height);
    expect(bottomRight.y - topLeft.y).toBeCloseTo(PANE.height, 4);
    // Centred horizontally.
    expect(topLeft.x).toBeCloseTo(
      (PANE.width - CONTENT.width * camera.scale) / 2,
      4,
    );
  });

  it("returns identity for a degenerate box instead of dividing by zero", () => {
    expect(fitAll({ x: 0, y: 0, width: 0, height: 0 }, PANE)).toEqual({
      scale: 1,
      tx: 0,
      ty: 0,
    });
  });
});

describe("fitWidth", () => {
  // This is the defect the camera exists to fix: a tall drawing in a short
  // pane must stay legible and scroll, not shrink to fit both axes.
  it("keeps a tall map readable instead of letterboxing it", () => {
    const both = fitAll(CONTENT, PANE, 0);
    const width = fitWidth(CONTENT, PANE, 0);
    expect(width.scale).toBeGreaterThan(both.scale);
    expect(width.scale).toBeCloseTo(1, 6); // 1300 wide pane, 1040 content: capped at 1:1
  });

  it("never magnifies past 1:1", () => {
    const camera = fitWidth({ x: 0, y: 0, width: 200, height: 100 }, PANE, 0);
    expect(camera.scale).toBe(1);
  });

  it("pins the top when the content overflows vertically", () => {
    const camera = fitWidth(CONTENT, PANE, 10);
    expect(project(camera, 0, 0).y).toBeCloseTo(10, 6);
  });

  it("centres vertically when the content is shorter than the pane", () => {
    const short = { x: 0, y: 0, width: 1040, height: 100 };
    const camera = fitWidth(short, PANE, 0);
    expect(project(camera, 0, 0).y).toBeCloseTo((600 - 100) / 2, 4);
  });
});

describe("zoomAt", () => {
  it("keeps the point under the cursor fixed", () => {
    const camera = { scale: 1, tx: 0, ty: 0 };
    const before = unproject(camera, 400, 260);
    const zoomed = zoomAt(camera, 400, 260, 2);
    const after = unproject(zoomed, 400, 260);
    expect(after.x).toBeCloseTo(before.x, 6);
    expect(after.y).toBeCloseTo(before.y, 6);
    expect(zoomed.scale).toBe(2);
  });

  it("clamps at both ends and stops changing there", () => {
    const hot = zoomAt({ scale: MAX_SCALE, tx: 0, ty: 0 }, 10, 10, 4);
    expect(hot.scale).toBe(MAX_SCALE);
    const cold = zoomAt({ scale: MIN_SCALE, tx: 0, ty: 0 }, 10, 10, 0.1);
    expect(cold.scale).toBe(MIN_SCALE);
  });
});

describe("clampScale", () => {
  it("rejects non-finite and non-positive input", () => {
    expect(clampScale(Number.NaN)).toBe(1);
    expect(clampScale(0)).toBe(1);
    expect(clampScale(-3)).toBe(1);
  });
});

describe("clampCamera", () => {
  it("always leaves some content on screen after a hard pan", () => {
    const camera = panBy(fitWidth(CONTENT, PANE, 0), -99999, -99999);
    const clamped = clampCamera(camera, CONTENT, PANE);
    const bottomRight = project(clamped, CONTENT.width, CONTENT.height);
    expect(bottomRight.x).toBeGreaterThan(0);
    expect(bottomRight.y).toBeGreaterThan(0);
  });

  it("leaves an in-view camera untouched", () => {
    const camera = fitWidth(CONTENT, PANE, 0);
    expect(clampCamera(camera, CONTENT, PANE)).toEqual(camera);
  });
});

describe("wheelZoomFactor", () => {
  it("zooms in on negative delta and out on positive", () => {
    expect(wheelZoomFactor(-100)).toBeGreaterThan(1);
    expect(wheelZoomFactor(100)).toBeLessThan(1);
  });

  it("bounds a single huge delta so one flick cannot cross the whole range", () => {
    expect(wheelZoomFactor(-100000)).toBeLessThan(1.5);
    expect(wheelZoomFactor(100000)).toBeGreaterThan(0.6);
  });
});
