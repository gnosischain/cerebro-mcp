// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GraphErrorBoundary } from "../GraphErrorBoundary";
import { GraphTableFallback } from "../GraphTableFallback";
import { TxSvgCanvas, type TxSvgTransaction } from "../canvas/TxSvgCanvas";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("GraphErrorBoundary", () => {
  it("renders the explicit fallback and resets on resetKey change", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const Broken = ({ fail }: { fail: boolean }) => {
      if (fail) throw new Error("regl failed");
      return <div data-renderer-ok="true">canvas</div>;
    };
    const render = async (resetKey: string, fail: boolean) => {
      await act(async () => {
        root.render(
          <GraphErrorBoundary
            resetKey={resetKey}
            fallback={(error) => <div role="alert">table: {error.message}</div>}
          >
            <Broken fail={fail} />
          </GraphErrorBoundary>,
        );
      });
    };

    await render("relationships:1", true);
    expect(container.querySelector("[role=alert]")?.textContent).toContain("regl failed");
    await render("relationships:2", false);
    expect(container.querySelector("[data-renderer-ok=true]")?.textContent).toBe("canvas");
    consoleError.mockRestore();
  });
});

describe("GraphTableFallback", () => {
  it("keeps node, edge and retry actions keyboard-native", async () => {
    const onNode = vi.fn();
    const onEdge = vi.fn();
    const onNodeAction = vi.fn();
    const onEdgeAction = vi.fn();
    const onRetry = vi.fn();
    await act(async () => {
      root.render(
        <GraphTableFallback
          error={new Error("WebGL unavailable")}
          model={{
            nodes: [{ id: "0xa", label: "Alice", kind: "address" }],
            edges: [{ id: "edge-1", source: "0xa", target: "0xb", label: "paid" }],
          }}
          onRetry={onRetry}
          onSelectNode={onNode}
          onSelectEdge={onEdge}
          onNodeAction={onNodeAction}
          nodeActionLabel="Investigate from here"
          onEdgeAction={onEdgeAction}
          edgeActionLabel="Open transactions"
        />,
      );
    });

    const buttons = [...container.querySelectorAll("button")];
    expect(buttons.every((button) => button.type === "button")).toBe(true);
    await act(async () => buttons.find((button) => button.textContent === "Alice")?.click());
    await act(async () => buttons.find((button) => button.textContent === "edge-1")?.click());
    await act(async () => buttons.find((button) => button.textContent?.includes("Retry"))?.click());
    await act(async () =>
      buttons.find((button) => button.textContent === "Investigate from here")?.click());
    await act(async () =>
      buttons.find((button) => button.textContent === "Open transactions")?.click());
    expect(onNode).toHaveBeenCalledWith("0xa");
    expect(onEdge).toHaveBeenCalledWith("edge-1");
    expect(onRetry).toHaveBeenCalledOnce();
    expect(onNodeAction).toHaveBeenCalledWith("0xa");
    expect(onEdgeAction).toHaveBeenCalledWith("edge-1");
    expect(container.querySelectorAll("th[scope=col]").length).toBeGreaterThan(0);
  });
});

describe("TxSvgCanvas", () => {
  it("renders one selectable wide hit path per selected transaction leg", async () => {
    const onSelectLeg = vi.fn();
    const transaction: TxSvgTransaction = {
      txHash: "0xtx",
      nodes: [{ id: "0xa" }, { id: "0xb" }],
      legs: [
        {
          id: "leg-1",
          source: "0xa",
          target: "0xb",
          txHash: "0xtx",
          logIndex: 4,
          tokenAddress: "0xtoken",
          amountUsd: null,
        },
      ],
    };
    await act(async () => {
      root.render(
        <TxSvgCanvas transaction={transaction} onSelectLeg={onSelectLeg} />,
      );
    });
    const hit = container.querySelector<SVGPathElement>(".tx-svg-hit");
    expect(hit?.getAttribute("stroke-width")).toBe("18");
    expect(hit?.getAttribute("tabindex")).toBe("0");
    expect(hit?.getAttribute("aria-label")).toContain("USD unknown");
    await act(async () => hit?.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(onSelectLeg).toHaveBeenCalledWith("leg-1");
    expect(container.querySelector("marker")).not.toBeNull();
    expect(container.querySelector("title")?.textContent).toContain("log 4");
  });

  it("keeps participant selection visually aligned with the inspector", async () => {
    const transaction: TxSvgTransaction = {
      txHash: "0xtx",
      nodes: [{ id: "0xa" }, { id: "0xb" }],
      legs: [
        {
          id: "leg-1",
          source: "0xa",
          target: "0xb",
          txHash: "0xtx",
          logIndex: 4,
          tokenAddress: "0xtoken",
          rawAmount: "1000000",
          amountUsd: null,
        },
      ],
    };
    await act(async () => {
      root.render(
        <TxSvgCanvas
          transaction={transaction}
          selectedNodeId="0xa"
          onSelectNode={vi.fn()}
        />,
      );
    });
    const selected = container.querySelector<SVGGElement>("[data-node-id='0xa']");
    expect(selected?.getAttribute("aria-pressed")).toBe("true");
    expect(selected?.querySelector("circle")?.getAttribute("stroke-width")).toBe("5");
    expect(container.querySelector("[data-node-id='0xb']")?.getAttribute("aria-pressed"))
      .toBe("false");
  });
});
