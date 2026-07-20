// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cameraSnapshotMatchesModel,
  captureCanvasCamera,
  positionsFromCameraSnapshot,
  restoreCanvasCamera,
  type CanvasCameraSnapshot,
} from "../canvas/CosmosCanvas";

afterEach(() => vi.restoreAllMocks());

describe("canvas camera persistence helpers", () => {
  const snapshot: CanvasCameraSnapshot = {
    zoom: 2.75,
    center: [410, 520],
    nodePositions: new Map([
      ["0xa", [10, 20]],
      ["0xb", [30, 40]],
    ]),
  };

  it("captures zoom, viewport center, and positions by stable node id", () => {
    const graph = {
      getZoomLevel: vi.fn(() => 2.75),
      screenToSpacePosition: vi.fn((): [number, number] => [410, 520]),
      getPointPositions: vi.fn(() => [10, 20, 30, 40]),
    };

    const captured = captureCanvasCamera(
      graph as never,
      { n: 2, indexToId: ["0xa", "0xb"] },
      { width: 800, height: 600 },
    );

    expect(graph.screenToSpacePosition).toHaveBeenCalledWith([400, 300]);
    expect(captured).toEqual(snapshot);
  });

  it("restores positions across row reordering and rejects another universe", () => {
    const reordered = positionsFromCameraSnapshot(
      {
        indexToId: ["0xb", "0xa", "0xc"],
        positions: Float32Array.from([1, 2, 3, 4, 5, 6]),
      },
      snapshot,
    );
    expect([...reordered]).toEqual([30, 40, 10, 20, 5, 6]);
    expect(cameraSnapshotMatchesModel(snapshot, { indexToId: ["0xb"] }))
      .toBe(true);
    expect(cameraSnapshotMatchesModel(snapshot, { indexToId: ["0xc"] }))
      .toBe(false);
  });

  it("centers then restores zoom after Cosmos commits its public transition", () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    const graph = {
      fitViewByPointPositions: vi.fn(),
      setZoomLevel: vi.fn(),
    };
    const complete = vi.fn();

    restoreCanvasCamera(graph as never, snapshot, complete);
    expect(graph.fitViewByPointPositions).toHaveBeenCalledWith([410, 520], 0);
    expect(graph.setZoomLevel).not.toHaveBeenCalled();
    frames.shift()?.(0);
    expect(graph.setZoomLevel).not.toHaveBeenCalled();
    frames.shift()?.(16);
    expect(graph.setZoomLevel).toHaveBeenCalledWith(2.75, 0);
    expect(complete).toHaveBeenCalledOnce();
  });
});
