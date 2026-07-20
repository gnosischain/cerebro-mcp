// @vitest-environment jsdom

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const harness = vi.hoisted(() => ({
  view: null as unknown,
  callTool: vi.fn((_name: string, _args: Record<string, unknown>) =>
    Promise.resolve(null),
  ),
  fetchRows: vi.fn(() => Promise.resolve(null)),
  updateModelContext: vi.fn(),
}));

vi.mock("../../shared/useMiniApp", () => ({
  useMiniApp: () => ({
    view: harness.view,
    callTool: harness.callTool,
    fetchRows: harness.fetchRows,
    updateModelContext: harness.updateModelContext,
  }),
}));

vi.mock("../../shared/useHydratedDatasets", () => ({
  useHydratedDatasets: () => ({}),
}));

vi.mock("../../shared/MiniAppChrome", () => ({
  MiniAppChrome: ({ children }: { children?: ReactNode }) => children,
}));
vi.mock("../../shared/HelpDialog", () => ({ MaHelpButton: () => null }));
vi.mock("../../shared/ToastStack", () => ({ ToastStack: () => null }));
vi.mock("../devFixture", () => ({ buildMockPayload: () => ({}) }));

vi.mock("../TaskSwitch", () => ({
  TASK_OF_MODE: {
    atlas: "relationships",
    investigate: "relationships",
    flows: "money",
    timeline: "money",
    transactions: "tx",
  },
  TaskSwitch: () => null,
}));

vi.mock("../modes/AtlasView", () => ({
  AtlasView: () => (
    <output data-testid="atlas-view">
      <details data-testid="atlas-catalog" open>
        <summary>Relationship filters</summary>
      </details>
    </output>
  ),
}));
vi.mock("../modes/FlowsView", () => ({ FlowsView: () => <div /> }));
vi.mock("../modes/InvestigateView", () => ({
  InvestigateView: () => <output data-testid="investigate-view" />,
}));
vi.mock("../modes/TransactionsView", () => ({ TransactionsView: () => <div /> }));
vi.mock("../modes/TimelineView", () => ({
  TimelineView: ({
    local,
  }: {
    local: {
      timelineGrain: string;
      timelineRangeDays: number;
      timelineWindowBuckets: number;
    };
  }) => (
    <output
      data-testid="timeline-state"
      data-grain={local.timelineGrain}
      data-range={local.timelineRangeDays}
      data-window={local.timelineWindowBuckets}
    />
  ),
}));

import GraphExplorerApp from "../GraphExplorerApp";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

function initialView() {
  return {
    type: "INITIAL_LOAD",
    view_id: "timeline-deep-link",
    datasets: {},
    view_state: {
      mode: "atlas",
      mode_revision: 0,
      dataset_revisions: {},
      selection: { node_id: "", edge_id: "", request_id: 0 },
      atlas: { selected_profiles: [], sample_size: 150, window_days: 90 },
      investigate: {
        seed: { id: "0xseed", kind: "address" },
        active_profiles: [],
        window_days: 90,
        max_neighbors: 100,
      },
      timeline: {
        grain: "week",
        range_days: 365,
        window_buckets: 4,
        profiles: [],
        range_start: "",
        anchor: { id: "", kind: "" },
      },
      flows: { seeds: [] },
      limits: {
        max_hops: 4,
        bfs_node_cap: 2_000,
        default_expand_depth: 1,
        ui_default_window_days: 90,
        ui_default_max_neighbors: 100,
        atlas_sample_size: 150,
        flows_default_hops: 2,
        flows_max_hops: 4,
        flows_default_min_usd: 10,
        flows_default_range_days: 90,
      },
    },
  };
}

describe("Timeline legacy deep-link boot", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    harness.callTool.mockClear();
    harness.fetchRows.mockClear();
    harness.updateModelContext.mockClear();
    harness.view = initialView();
    window.history.replaceState(
      {},
      "",
      "/graph-explorer.html?mode=timeline&tgrain=month&trange=730&twin=8&fseeds=0xaaa,0xbbb&token=dev",
    );
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it("enqueues one URL-resolved scope and adopts the playback window", async () => {
    await act(async () => {
      root.render(<GraphExplorerApp />);
      await Promise.resolve();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const timelineLoads = harness.callTool.mock.calls.filter(
      ([name]) => name === "load_graph_timeline",
    );
    expect(timelineLoads).toHaveLength(1);
    expect(timelineLoads[0]?.[1]).toMatchObject({
      view_id: "timeline-deep-link",
      grain: "month",
      range_days: 730,
      seed_node_ids: ["0xaaa", "0xbbb"],
      request_id: 1,
    });
    expect(
      harness.callTool.mock.calls.filter(([name]) => name === "load_graph_flows"),
    ).toHaveLength(0);

    const state = container.querySelector<HTMLOutputElement>(
      "[data-testid='timeline-state']",
    );
    expect(state?.dataset).toMatchObject({
      grain: "month",
      range: "730",
      window: "8",
    });

    const url = new URL(window.location.href);
    expect(url.searchParams.get("tgrain")).toBe("month");
    expect(url.searchParams.get("trange")).toBe("730");
    expect(url.searchParams.get("twin")).toBe("8");
    expect(url.searchParams.get("mode")).toBe("timeline");
    expect(url.searchParams.get("fseeds")).toBe("0xaaa,0xbbb");
    expect(url.searchParams.get("token")).toBe("dev");
  });

  it("uses a Money Trail seed and lookback without falling back to Trail", async () => {
    window.history.replaceState(
      {},
      "",
      "/graph-explorer.html?mode=timeline&fseeds=0xaaa&frange=90&token=dev",
    );

    await act(async () => {
      root.render(<GraphExplorerApp />);
      await Promise.resolve();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const timelineLoads = harness.callTool.mock.calls.filter(
      ([name]) => name === "load_graph_timeline",
    );
    expect(timelineLoads).toHaveLength(1);
    expect(timelineLoads[0]?.[1]).toMatchObject({
      seed_node_ids: ["0xaaa"],
      range_days: 90,
      direction: "out",
      request_id: 1,
    });
    expect(
      harness.callTool.mock.calls.filter(([name]) => name === "load_graph_flows"),
    ).toHaveLength(0);
    expect(container.querySelector("[data-testid='timeline-state']")).not.toBeNull();
    expect(new URL(window.location.href).searchParams.get("mode")).toBe("timeline");
  });

  it("keeps an explicit Atlas route authoritative over persisted Investigate state", async () => {
    const persisted = initialView();
    persisted.view_state.mode = "investigate";
    persisted.view_state.mode_revision = 4;
    harness.view = persisted;
    window.history.replaceState(
      {},
      "",
      "/graph-explorer.html?mode=atlas&seed=0xseed&token=dev",
    );

    await act(async () => {
      root.render(<GraphExplorerApp />);
      await Promise.resolve();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.querySelector("[data-testid='atlas-view']")).not.toBeNull();
    const focusCalls = harness.callTool.mock.calls.filter(
      ([name]) => name === "update_graph_explorer_focus",
    );
    expect(focusCalls).toHaveLength(1);
    expect(focusCalls[0]?.[1]).toMatchObject({
      view_id: "timeline-deep-link",
      mode: "atlas",
      selected_node_id: "",
      selected_edge_id: "",
      request_id: 1,
    });

    const url = new URL(window.location.href);
    expect(url.searchParams.get("mode")).toBeNull();
    expect(url.searchParams.get("token")).toBe("dev");
  });

  it("does not enter Investigate when an Atlas URL seed is absent from the first payload", async () => {
    const beforeSeedSync = initialView();
    beforeSeedSync.view_state.investigate.seed.id = "";
    beforeSeedSync.view_state.mode = "atlas";
    beforeSeedSync.view_state.mode_revision = 0;
    harness.view = beforeSeedSync;
    window.history.replaceState(
      {},
      "",
      "/graph-explorer.html?mode=atlas&seed=0xdeep-linked&token=dev",
    );

    await act(async () => {
      root.render(<GraphExplorerApp />);
      await Promise.resolve();
    });
    // The serialized relationship loader resolves asynchronously. This was
    // the failing server-sync boundary: it used to dispatch Investigate after
    // the explicit Atlas route had already rendered.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.querySelector("[data-testid='atlas-view']")).not.toBeNull();
    expect(container.querySelector("[data-testid='investigate-view']")).toBeNull();
    expect(
      container.querySelector<HTMLDetailsElement>("[data-testid='atlas-catalog']")
        ?.open,
    ).toBe(true);

    const seedLoads = harness.callTool.mock.calls.filter(
      ([name]) => name === "load_graph_explorer_seed",
    );
    expect(seedLoads).toHaveLength(1);
    expect(seedLoads[0]?.[1]).toMatchObject({
      view_id: "timeline-deep-link",
      seed_node_id: "0xdeep-linked",
    });

    const url = new URL(window.location.href);
    expect(url.searchParams.get("mode")).not.toBe("investigate");
    expect(url.searchParams.get("token")).toBe("dev");
  });
});
