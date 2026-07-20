// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  GraphCanvas,
  resetCanvasSessionStateForTests,
} from "../canvas/GraphCanvas";
import type { CanvasCameraSnapshot } from "../canvas/CosmosCanvas";
import { buildGraphModel } from "../model/parseRows";

const { canvasProps } = vi.hoisted(() => ({ canvasProps: [] as unknown[] }));

vi.mock("../canvas/CosmosCanvas", () => ({
  CosmosCanvas: (props: unknown) => {
    canvasProps.push(props);
    return null;
  },
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

interface MockCanvasProps {
  initialCamera?: CanvasCameraSnapshot | null;
  onCameraStateChange?: (camera: CanvasCameraSnapshot) => void;
}

const model = buildGraphModel(
  [
    ["0xa", "address", "Alice", ["paid"]],
    ["0xb", "address", "Bob", ["paid"]],
  ],
  [["edge-1", "0xa", "0xb", "paid", 12, 1, true]],
  ["paid"],
  { profileSelectionPhase: "applied" },
);

const camera: CanvasCameraSnapshot = {
  zoom: 3,
  center: [100, 200],
  nodePositions: new Map([
    ["0xa", [10, 20]],
    ["0xb", [30, 40]],
  ]),
};

let container: HTMLDivElement;
let root: Root;

const renderCanvas = (stateKey: string) => (
  <GraphCanvas
    stateKey={stateKey}
    model={model}
    selectedNodeId=""
    emptyHint="No graph rows"
    onSelectNode={vi.fn()}
    onSelectEdge={vi.fn()}
    onExpandNode={vi.fn()}
  />
);

const latestProps = (): MockCanvasProps =>
  canvasProps[canvasProps.length - 1] as MockCanvasProps;

beforeEach(() => {
  resetCanvasSessionStateForTests();
  canvasProps.length = 0;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("GraphCanvas task-keyed camera state", () => {
  it("restores the captured camera when the same task key remounts", async () => {
    await act(async () => root.render(renderCanvas("relationships:atlas")));
    expect(latestProps().initialCamera).toBeNull();
    await act(async () => latestProps().onCameraStateChange?.(camera));

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(renderCanvas("relationships:atlas")));

    expect(latestProps().initialCamera).toBe(camera);
  });

  it("does not leak a camera into a different task key", async () => {
    await act(async () => root.render(renderCanvas("relationships:atlas")));
    await act(async () => latestProps().onCameraStateChange?.(camera));

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(renderCanvas("money:trail")));

    expect(latestProps().initialCamera).toBeNull();
  });
});
