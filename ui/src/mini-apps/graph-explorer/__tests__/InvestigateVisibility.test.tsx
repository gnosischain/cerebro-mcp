// @vitest-environment jsdom

import { act, useReducer } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InvestigateView } from "../modes/InvestigateView";
import { buildInitialState, graphReducer } from "../state/graphReducer";
import type { GraphExplorerViewState } from "../types";
import type { HydratedDataset } from "../../shared/useHydratedDatasets";

vi.mock("../canvas/GraphCanvas", () => ({
  GraphCanvas: (props: {
    visibleProfiles?: ReadonlySet<string>;
    onToggleProfileVisibility?: (profile: string, visible: boolean) => void;
  }) => (
    <div data-testid="graph" data-visible={[...(props.visibleProfiles ?? [])].sort().join(",")}>
      <button
        type="button"
        onClick={() => props.onToggleProfileVisibility?.("p2", false)}
      >
        hide p2
      </button>
    </div>
  ),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const server = {
  investigate: {
    seed: { id: "0xa", kind: "address" },
    active_profiles: ["p1", "p2"],
    window_days: 90,
    max_neighbors: 100,
    hops_used: 1,
    scope: { scope_id: "investigate:1" },
  },
  catalog: [],
  node_roles: {},
  suggested_next_hops: [],
} as unknown as GraphExplorerViewState;

function hydrated(rows: unknown[][]): HydratedDataset {
  return {
    rows,
    columns: [],
    columnTypes: [],
    truncated: false,
    phase: "complete",
    rowsLoaded: rows.length,
    rowsExpected: rows.length,
    error: null,
    hydrating: false,
  };
}

function Harness() {
  const [local, dispatch] = useReducer(graphReducer, buildInitialState(server));
  return (
    <InvestigateView
      server={server}
      local={local}
      dispatch={dispatch}
      nodes={hydrated([
        ["0xa", "address", "A", ["p1", "p2"]],
        ["0xb", "address", "B", ["p1"]],
        ["0xc", "safe", "C", ["p2"]],
      ])}
      edges={hydrated([
        ["edge-p1", "0xa", "0xb", "p1", 20, 1, true],
        ["edge-p2", "0xa", "0xc", "p2", 10, 1, true],
      ])}
      nodeEvidence={undefined}
      edgeEvidence={undefined}
      evidenceExpectation={null}
      refetchSeed={vi.fn()}
      seedInvestigate={vi.fn()}
      expandNode={vi.fn()}
      loading={false}
      loadError={null}
      onSelectNode={vi.fn()}
      onSelectEdge={vi.fn()}
      onClearSelection={vi.fn()}
      onBrowseAtlas={vi.fn()}
    />
  );
}

describe("Investigate controlled relationship visibility", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 900,
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it("uses the same set for the graph and ranked-neighbour table", async () => {
    await act(async () => root.render(<Harness />));
    expect(container.querySelector("[data-testid=graph]")?.getAttribute("data-visible"))
      .toBe("p1,p2");
    expect(container.querySelector(".ge-body")?.classList.contains("details-open"))
      .toBe(false);
    expect(container.querySelector(".ge-ranked-table__rows")?.textContent)
      .toContain("p2");

    await act(async () => {
      [...container.querySelectorAll("button")]
        .find((button) => button.textContent === "hide p2")
        ?.click();
    });

    expect(container.querySelector("[data-testid=graph]")?.getAttribute("data-visible"))
      .toBe("p1");
    expect(container.querySelector(".ge-ranked-table__rows")?.textContent)
      .not.toContain("p2");
    expect(container.querySelector(".ge-ranked-table__rows")?.textContent)
      .toContain("p1");
  });
});
