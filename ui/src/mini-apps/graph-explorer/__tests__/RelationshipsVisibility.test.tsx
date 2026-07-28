// @vitest-environment jsdom

import { act, useReducer } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RelationshipsView } from "../modes/RelationshipsView";
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

// Same fixture, but p2 is a *candidate* profile and the view opens with the
// "approved" status filter — so p2 is an ACTIVE profile that the filter hides
// from the canvas. The user must be told, or the graph silently under-reports.
const trimmingServer = {
  ...server,
  semantic_status_filter: "approved",
  catalog: [
    {
      profile: "p1",
      semantic_status: "approved",
      question_synonyms: [],
      source_kind: "address",
      target_kind: "address",
    },
    {
      profile: "p2",
      semantic_status: "candidate",
      question_synonyms: [],
      source_kind: "address",
      target_kind: "address",
    },
  ],
} as unknown as GraphExplorerViewState;

function Harness({
  view = server,
}: {
  view?: GraphExplorerViewState;
}) {
  const [local, dispatch] = useReducer(graphReducer, buildInitialState(view));
  return (
    <RelationshipsView
      server={view}
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
      atlasNodes={undefined}
      atlasEdges={undefined}
      atlasPreviewNodes={undefined}
      atlasPreviewEdges={undefined}
      loadSample={vi.fn()}
      loadPreview={vi.fn()}
      previewLoading={false}
      previewError={null}
      desiredPreviewRequestId={0}
      refetchSeed={vi.fn()}
      seedInvestigate={vi.fn()}
      expandNode={vi.fn()}
      loading={false}
      loadError={null}
      onSelectNode={vi.fn()}
      onSelectEdge={vi.fn()}
      onClearSelection={vi.fn()}
    />
  );
}

describe("Relationships controlled relationship visibility", () => {
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

  it("discloses active profiles the status filter hides from the canvas", async () => {
    await act(async () => root.render(<Harness view={trimmingServer} />));

    // p2 is active but filtered out: the canvas must not silently show less
    // than the applied selection.
    expect(container.querySelector("[data-testid=graph]")?.getAttribute("data-visible"))
      .toBe("p1");
    const note = container.querySelector(".ge-scope-strip__filter");
    expect(note).not.toBeNull();
    expect(note?.textContent).toContain("1");
    expect(note?.textContent).toContain("approved");
  });
});
