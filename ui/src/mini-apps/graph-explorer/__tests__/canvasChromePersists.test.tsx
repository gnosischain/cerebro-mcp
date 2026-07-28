// @vitest-environment jsdom
//
// Regression guard for the reported defect: "text in the middle of the graph
// and it is not clear how to navigate". When the model was empty the canvas
// chrome was unmounted entirely (`model.n > 0 ? <chrome/> : null`), so the
// search box, Fit view, the force controls, the stats chip and the legend
// button all disappeared at the exact moment the user needed them — leaving a
// centred sentence and nothing to click.
//
// The chrome is now unconditional, and the centred hint no longer swallows
// pointer events from anything rendered inside it.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GraphCanvas } from "../canvas/GraphCanvas";
import { buildGraphModel } from "../model/parseRows";

vi.mock("../canvas/CosmosCanvas", () => ({
  CosmosCanvas: (props: { emptyHint?: React.ReactNode; model: { n: number } }) => (
    <div data-testid="cosmos">
      {props.model.n === 0 ? (
        <div className="ge-placeholder">
          <div className="ge-placeholder__body">{props.emptyHint}</div>
        </div>
      ) : null}
    </div>
  ),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const EMPTY_MODEL = buildGraphModel([], [], []);
const FULL_MODEL = buildGraphModel(
  [
    ["0xa", "address", "A", ["p"]],
    ["0xb", "address", "B", ["p"]],
  ],
  [["e1", "0xa", "0xb", "p", 1, 1, true]],
  ["p"],
);

describe("canvas chrome persists through emptiness", () => {
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

  const render = async (model: typeof EMPTY_MODEL, hint: React.ReactNode) => {
    await act(async () => {
      root.render(
        <GraphCanvas
          model={model}
          emptyHint={hint}
          selectedNodeId=""
          onSelectNode={vi.fn()}
          onSelectEdge={vi.fn()}
          onExpandNode={vi.fn()}
          stats={{
            nodeCount: model.n,
            edgeCount: model.edgeRows.length,
            hopsUsed: 0,
            maxHops: 5,
            activeProfileCount: 0,
            catalogSize: 3,
          }}
        />,
      );
    });
  };

  it("keeps the toolbar and stats chip mounted when the model is empty", async () => {
    await render(EMPTY_MODEL, "nothing here");

    expect(container.querySelector(".ge-graph-chrome")).not.toBeNull();
    expect(container.querySelector(".ge-graph-controls")).not.toBeNull();
    // Fit view / Focus / search are present, and inert rather than absent.
    const fit = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "Fit view",
    );
    expect(fit).toBeDefined();
    expect(fit?.disabled).toBe(true);
    const search = container.querySelector<HTMLInputElement>(
      ".ge-graph-search input",
    );
    expect(search).not.toBeNull();
    expect(search?.disabled).toBe(true);
  });

  it("re-enables the same controls once rows arrive", async () => {
    await render(FULL_MODEL, "nothing here");
    const fit = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "Fit view",
    );
    expect(fit?.disabled).toBe(false);
    expect(container.querySelector(".ge-graph-search input")?.hasAttribute("disabled"))
      .toBe(false);
  });

  it("renders an interactive empty state, not just a sentence", async () => {
    const onClick = vi.fn();
    await render(
      EMPTY_MODEL,
      <button type="button" onClick={onClick}>
        Explore →
      </button>,
    );

    const action = [...container.querySelectorAll("button")].find(
      (button) => button.textContent === "Explore →",
    );
    expect(action).toBeDefined();
    await act(async () => action?.click());
    expect(onClick).toHaveBeenCalled();
  });
});
