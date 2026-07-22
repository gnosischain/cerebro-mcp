// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  GraphCanvas,
  resetCanvasSessionStateForTests,
} from "../canvas/GraphCanvas";
import { buildGraphModel } from "../model/parseRows";

const { constructGraph } = vi.hoisted(() => ({
  constructGraph: vi.fn(),
}));

vi.mock("@cosmos.gl/graph", () => ({
  Graph: class {
    constructor(_container: HTMLElement, config: unknown) {
      constructGraph(config);
    }
    destroy() {}
    setPointPositions() {}
    setPointColors() {}
    setPointSizes() {}
    setLinks() {}
    setLinkWidths() {}
    setLinkColors() {}
    setLinkArrows() {}
    render() {}
    start() {}
    setConfig() {}
    unselectPoints() {}
    trackPointPositionsByIndices() {}
  },
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  resetCanvasSessionStateForTests();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  constructGraph.mockClear();
  Reflect.deleteProperty(window, "WebGLRenderingContext");
});

describe("GraphCanvas renderer isolation", () => {
  const model = () => buildGraphModel(
    [
      ["0xa", "address", "Alice", ["paid"]],
      ["0xb", "address", "Bob", ["paid"]],
    ],
    [["edge-1", "0xa", "0xb", "paid", 12, 1, true]],
    ["paid"],
    { profileSelectionPhase: "applied" },
  );

  it("routes missing WebGL to the selectable table without removing controls", async () => {
    const onSelectNode = vi.fn();
    const onSelectEdge = vi.fn();
    const onExpandNode = vi.fn();
    const onEdgeAction = vi.fn();

    await act(async () => {
      root.render(
        <GraphCanvas
          model={model()}
          selectedNodeId="0xa"
          selectedEdgeId="edge-1"
          seedNodeId="0xa"
          emptyHint="No graph rows"
          onSelectNode={onSelectNode}
          onSelectEdge={onSelectEdge}
          onExpandNode={onExpandNode}
          onFallbackEdgeAction={onEdgeAction}
        />,
      );
    });

    const fallback = container.querySelector("[data-graph-table-fallback=true]");
    expect(fallback).not.toBeNull();
    expect(fallback?.textContent).toContain("WebGL is unavailable");
    expect(container.textContent).toContain("Fit view");
    const chrome = container.querySelector(".ge-graph-chrome");
    const stage = container.querySelector(".ge-graph-stage");
    expect(chrome).not.toBeNull();
    expect(stage).not.toBeNull();
    expect(stage?.querySelector(".ge-graph-controls")).toBeNull();
    expect(stage?.querySelector(".ge-legend")).toBeNull();
    expect(stage?.querySelector(".ge-canvas-stats")).toBeNull();
    expect(
      Boolean(
        chrome && stage &&
          (chrome.compareDocumentPosition(stage) & Node.DOCUMENT_POSITION_FOLLOWING),
      ),
    ).toBe(true);
    expect(container.querySelector("tr[data-node-id='0xa']")?.getAttribute("aria-selected"))
      .toBe("true");
    expect(container.querySelector("tr[data-edge-id='edge-1']")?.getAttribute("aria-selected"))
      .toBe("true");

    const alice = container.querySelector<HTMLButtonElement>(
      "button[aria-label='Select 0xa']",
    );
    const edge = container.querySelector<HTMLButtonElement>(
      "button[aria-label='Select edge-1']",
    );
    const investigate = container.querySelector<HTMLButtonElement>(
      "button[aria-label='Investigate from here: 0xa']",
    );
    const openTransactions = container.querySelector<HTMLButtonElement>(
      "button[aria-label='Open transactions: edge-1']",
    );
    await act(async () => alice?.click());
    await act(async () => edge?.click());
    await act(async () => investigate?.click());
    await act(async () => openTransactions?.click());
    expect(onSelectNode).toHaveBeenCalledWith("0xa");
    expect(onSelectEdge).toHaveBeenCalledWith("edge-1");
    expect(onExpandNode).toHaveBeenCalledWith("0xa");
    expect(onEdgeAction).toHaveBeenCalledWith("edge-1");
  });

  it("routes Cosmos initialization errors through the same isolated fallback", async () => {
    Object.defineProperty(window, "WebGLRenderingContext", {
      configurable: true,
      value: function WebGLRenderingContext() {},
    });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue({} as RenderingContext);
    constructGraph.mockImplementationOnce(() => {
      throw new Error("regl initialization failed");
    });

    await act(async () => {
      root.render(
        <GraphCanvas
          model={model()}
          selectedNodeId=""
          emptyHint="No graph rows"
          onSelectNode={vi.fn()}
          onSelectEdge={vi.fn()}
          onExpandNode={vi.fn()}
        />,
      );
    });

    expect(constructGraph).toHaveBeenCalledOnce();
    const fallback = container.querySelector("[data-graph-table-fallback=true]");
    expect(fallback?.textContent).toContain("initialization failed");
    expect(fallback?.textContent).toContain("regl initialization failed");
    expect(container.textContent).toContain("Fit view");

    const retry = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent?.includes("Retry visual renderer"));
    expect(retry?.textContent).toContain("Retry visual renderer");
    await act(async () => retry?.click());
    expect(container.querySelector("[data-graph-table-fallback=true]"))
      .toBeNull();
    expect(constructGraph).toHaveBeenCalledTimes(2);
  });

  it("uses one controlled profile set for the legend and fallback graph", async () => {
    const onToggleProfileVisibility = vi.fn();
    await act(async () => {
      root.render(
        <GraphCanvas
          model={model()}
          selectedNodeId=""
          emptyHint="No graph rows"
          visibleProfiles={new Set()}
          onToggleProfileVisibility={onToggleProfileVisibility}
          onSelectNode={vi.fn()}
          onSelectEdge={vi.fn()}
          onExpandNode={vi.fn()}
        />,
      );
    });

    expect(container.querySelector("tr[data-edge-id='edge-1']")).toBeNull();
    const paid = [...container.querySelectorAll<HTMLButtonElement>(
      ".ge-legend-item",
    )].find((button) => button.textContent?.includes("paid"));
    expect(paid?.classList.contains("off")).toBe(true);
    await act(async () => paid?.click());
    expect(onToggleProfileVisibility).toHaveBeenCalledWith("paid", true);
  });

  it("routes an asynchronous WebGL context loss without destroying controls", async () => {
    Object.defineProperty(window, "WebGLRenderingContext", {
      configurable: true,
      value: function WebGLRenderingContext() {},
    });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue({} as RenderingContext);

    await act(async () => {
      root.render(
        <GraphCanvas
          model={model()}
          selectedNodeId=""
          emptyHint="No graph rows"
          onSelectNode={vi.fn()}
          onSelectEdge={vi.fn()}
          onExpandNode={vi.fn()}
        />,
      );
    });
    expect(container.querySelector("[data-graph-table-fallback=true]"))
      .toBeNull();

    const renderer = container.querySelector(".ge-cosmos-canvas");
    await act(async () => {
      renderer?.dispatchEvent(new Event("webglcontextlost", { bubbles: false }));
    });

    expect(container.querySelector("[data-graph-table-fallback=true]")?.textContent)
      .toContain("WebGL context was lost");
    expect(container.textContent).toContain("Fit view");

    // A new immutable model is a new dataset revision. It must retire the old
    // renderer failure without requiring a page reload.
    await act(async () => {
      root.render(
        <GraphCanvas
          model={model()}
          selectedNodeId=""
          emptyHint="No graph rows"
          onSelectNode={vi.fn()}
          onSelectEdge={vi.fn()}
          onExpandNode={vi.fn()}
        />,
      );
    });
    expect(container.querySelector("[data-graph-table-fallback=true]"))
      .toBeNull();
    expect(constructGraph).toHaveBeenCalledTimes(2);
  });

  it("updates an immutable model without recreating the WebGL renderer", async () => {
    Object.defineProperty(window, "WebGLRenderingContext", {
      configurable: true,
      value: function WebGLRenderingContext() {},
    });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue({} as RenderingContext);

    await act(async () => root.render(
      <GraphCanvas
        model={model()}
        selectedNodeId=""
        emptyHint="No graph rows"
        onSelectNode={vi.fn()}
        onSelectEdge={vi.fn()}
        onExpandNode={vi.fn()}
      />,
    ));
    expect(constructGraph).toHaveBeenCalledOnce();

    await act(async () => root.render(
      <GraphCanvas
        model={model()}
        selectedNodeId="0xa"
        emptyHint="No graph rows"
        onSelectNode={vi.fn()}
        onSelectEdge={vi.fn()}
        onExpandNode={vi.fn()}
      />,
    ));
    expect(constructGraph).toHaveBeenCalledOnce();
  });

  it("restores task-keyed canvas controls after the view unmounts", async () => {
    const render = () => (
      <GraphCanvas
        stateKey="relationships:investigate:test"
        model={model()}
        selectedNodeId=""
        emptyHint="No graph rows"
        onSelectNode={vi.fn()}
        onSelectEdge={vi.fn()}
        onExpandNode={vi.fn()}
        stats={{
          nodeCount: 2,
          edgeCount: 1,
          hopsUsed: 1,
          maxHops: 4,
          activeProfileCount: 1,
          catalogSize: 1,
        }}
      />
    );

    await act(async () => root.render(render()));
    const search = container.querySelector<HTMLInputElement>(
      "input[placeholder*='Find node']",
    );
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set;
    await act(async () => {
      setter?.call(search, "Alice");
      search?.dispatchEvent(new Event("input", { bubbles: true }));
      [...container.querySelectorAll("button")]
        .find((button) => button.textContent === "Focus")
        ?.click();
      [...container.querySelectorAll("button")]
        .find((button) => button.textContent?.startsWith("Legend"))
        ?.click();
      container.querySelector<HTMLButtonElement>(
        "button[aria-label='Graph statistics — click to hide']",
      )?.click();
    });

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(render()));

    expect(container.querySelector<HTMLInputElement>(
      "input[placeholder*='Find node']",
    )?.value).toBe("Alice");
    expect([...container.querySelectorAll("button")]
      .find((button) => button.textContent === "Focus")
      ?.getAttribute("aria-pressed")).toBe("true");
    expect([...container.querySelectorAll("button")]
      .find((button) => button.textContent?.startsWith("Legend"))
      ?.textContent).toContain("▸");
    expect(container.querySelector(
      "button[aria-label='Show graph stats']",
    )).not.toBeNull();
  });
});
