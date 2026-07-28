// @vitest-environment jsdom

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const harness = vi.hoisted(() => ({
  seeds: [] as number[],
  view: null as unknown,
  callTool: vi.fn(() => Promise.resolve(null)),
}));

vi.mock("../../shared/useMiniApp", () => ({
  useMiniApp: () => ({
    view: harness.view,
    callTool: harness.callTool,
    fetchRows: vi.fn(() => Promise.resolve(null)),
    updateModelContext: vi.fn(),
  }),
}));

vi.mock("../../shared/useHydratedDatasets", () => ({
  useHydratedDatasets: () => ({}),
}));

vi.mock("../state/useSerializedLoader", () => ({
  useSerializedLoader: (
    _send: unknown,
    _onError: unknown,
    initialRequestId = 0,
  ) => {
    harness.seeds.push(initialRequestId);
    return {
      enqueue: vi.fn(() => initialRequestId + 1),
      loading: false,
      desiredRequestId: initialRequestId,
      activeRequestId: null,
      error: null,
    };
  },
}));

vi.mock("../../shared/MiniAppChrome", () => ({
  MiniAppChrome: ({ children }: { children?: ReactNode }) => children,
}));
vi.mock("../../shared/HelpDialog", () => ({ MaHelpButton: () => null }));
vi.mock("../../shared/ToastStack", () => ({ ToastStack: () => null }));
vi.mock("../devFixture", () => ({ buildMockPayload: () => ({}) }));
vi.mock("../GraphNav", () => ({
  TASK_OF_MODE: {
    atlas: "relationships",
    investigate: "relationships",
    flows: "money",
    timeline: "money",
    transactions: "tx",
  },
  GraphNav: () => null,
}));
vi.mock("../modes/AtlasView", () => ({ AtlasView: () => <div /> }));
vi.mock("../modes/FlowsView", () => ({ FlowsView: () => <div /> }));
vi.mock("../modes/InvestigateView", () => ({ InvestigateView: () => <div /> }));
vi.mock("../modes/TimelineView", () => ({ TimelineView: () => <div /> }));
vi.mock("../modes/TransactionsView", () => ({ TransactionsView: () => <div /> }));

import GraphExplorerApp from "../GraphExplorerApp";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

function scope(requestId: number) {
  return { request_id: requestId };
}

describe("Graph Explorer loader revision seeding", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    harness.seeds = [];
    harness.callTool.mockClear();
    harness.view = {
      type: "INITIAL_LOAD",
      view_id: "revisioned-view",
      datasets: {},
      view_state: {
        mode: "atlas",
        mode_revision: 0,
        dataset_revisions: {},
        selection: { node_id: "", edge_id: "", request_id: 7 },
        atlas: {
          selected_profiles: [],
          sample_size: 25,
          window_days: 90,
          scope: scope(10),
        },
        atlas_preview: { scope: scope(12) },
        investigate: {
          seed: { id: "", kind: "" },
          active_profiles: [],
          window_days: 90,
          max_neighbors: 100,
          scope: scope(11),
        },
        timeline: {
          grain: "week",
          range_days: 365,
          window_buckets: 4,
          profiles: [],
          range_start: "",
          anchor: { id: "", kind: "" },
          forensic_scope: scope(21),
        },
        flows: { seeds: [], scope: scope(31) },
        transactions: {
          scope: scope(40),
          discovery_scope: scope(41),
          receipt_scope: scope(42),
          last_attempt: { request_id: 43, status: "failed" },
        },
        limits: {
          max_hops: 4,
          bfs_node_cap: 2_000,
          default_expand_depth: 1,
          ui_default_window_days: 90,
          ui_default_max_neighbors: 100,
          atlas_sample_size: 25,
        },
      },
    };
    window.history.replaceState({}, "", "/graph-explorer.html");
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it("seeds timeline, money, and transaction queues past applied server work", async () => {
    await act(async () => {
      root.render(<GraphExplorerApp />);
      await Promise.resolve();
    });

    // Hook order: focus, applied Relationships, preview, Timeline, Money Trail,
    // Transaction Detail. Transaction seeding includes failed attempts because
    // the backend reserves a revision before preserving the older evidence.
    expect(harness.seeds.slice(0, 6)).toEqual([7, 11, 12, 21, 31, 43]);
  });
});
