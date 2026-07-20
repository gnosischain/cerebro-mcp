// @vitest-environment jsdom
// Mode authority and the generic persistence safety boundary. Data-backed
// drafts must wait for their loaders; only visual preferences may use the
// debounced set_graph_explorer_view channel.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { useGraphSync } from "../state/useGraphSync";
import type { GraphExplorerViewState } from "../types";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const callToolMock = vi.fn(
  async (_name: string, _args: Record<string, unknown>): Promise<unknown> => null,
);
const callTool = <T = unknown>(
  name: string,
  args: Record<string, unknown>,
): Promise<T | null> => callToolMock(name, args) as Promise<T | null>;

function server(over: Partial<GraphExplorerViewState>): GraphExplorerViewState {
  return {
    title: "Graph Explorer",
    mode: "atlas",
    mode_revision: 0,
    catalog: [],
    limits: {
      max_hops: 7, bfs_node_cap: 2000, default_expand_depth: 1,
      ui_default_window_days: 90, ui_default_max_neighbors: 100, atlas_sample_size: 150,
    },
    atlas: { selected_profiles: [], sample_size: 150, window_days: 90 },
    investigate: {
      seed: { id: "", kind: "" }, active_profiles: [], window_days: 90,
      max_neighbors: 100, hops_used: 0,
    },
    selection: { node_id: "", edge_id: "" },
    layout: "force",
    semantic_status_filter: "all",
    node_roles: {},
    suggested_next_hops: [],
    warnings: [],
    ...over,
  };
}

let handle: ReturnType<typeof useGraphSync>;

function Harness(props: { server: GraphExplorerViewState; revKey: string }) {
  handle = useGraphSync("view-1", props.server, props.revKey, callTool);
  return null;
}

let root: Root;
beforeEach(async () => {
  vi.useFakeTimers();
  callToolMock.mockClear();
  const el = document.createElement("div");
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root.render(<Harness server={server({ mode: "atlas", mode_revision: 0 })} revKey="v0" />);
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.replaceChildren();
  vi.useRealTimers();
});

async function flushSyncDebounce() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(301);
  });
}

describe("useGraphSync mode authority", () => {
  it("a stale cross-mode data load (same mode_revision) does not flip the tab", async () => {
    // User clicks Timeline.
    await act(async () => {
      handle.dispatch({ type: "SET_MODE", mode: "timeline" });
    });
    expect(handle.state.mode).toBe("timeline");

    // A slow flows trace lands: server says flows, but mode_revision is
    // unchanged and a dataset revision bumped (new revKey).
    await act(async () => {
      root.render(
        <Harness server={server({ mode: "flows", mode_revision: 0 })} revKey="v1" />,
      );
    });
    expect(handle.state.mode).toBe("timeline"); // the bug, fixed
  });

  it("a deliberate mode command (advanced mode_revision) switches the tab", async () => {
    await act(async () => {
      handle.dispatch({ type: "SET_MODE", mode: "timeline" });
    });
    await act(async () => {
      root.render(
        <Harness server={server({ mode: "flows", mode_revision: 1 })} revKey="v2" />,
      );
    });
    expect(handle.state.mode).toBe("flows");
  });

  it("an evidence-only revision bump (same mode_revision) stays put", async () => {
    await act(async () => {
      handle.dispatch({ type: "SET_MODE", mode: "investigate" });
    });
    await act(async () => {
      root.render(
        <Harness server={server({ mode: "flows", mode_revision: 0 })} revKey="v-evidence" />,
      );
    });
    expect(handle.state.mode).toBe("investigate");
  });
});

describe("useGraphSync persistence boundary", () => {
  it("keeps window/profile edits local until a scoped loader accepts them", async () => {
    await act(async () => {
      handle.dispatch({ type: "SET_WINDOW", days: 365 });
      handle.dispatch({ type: "SET_ATLAS_PROFILES", profiles: ["circles_trust"] });
    });
    await flushSyncDebounce();

    expect(handle.state.windowDays).toBe(365);
    expect(handle.state.atlasProfiles).toEqual(["circles_trust"]);
    expect(callToolMock).not.toHaveBeenCalled();
  });

  it("persists layout/status without mode or data namespaces", async () => {
    await act(async () => {
      handle.dispatch({ type: "SET_LAYOUT", layout: "circular" });
      handle.dispatch({ type: "SET_STATUS_FILTER", filter: "approved" });
    });
    await flushSyncDebounce();

    expect(callToolMock).toHaveBeenCalledTimes(1);
    expect(callToolMock).toHaveBeenCalledWith("set_graph_explorer_view", {
      view_id: "view-1",
      patch: {
        layout: "circular",
        semantic_status_filter: "approved",
      },
    });
    const patch = callToolMock.mock.calls[0]?.[1]?.patch;
    expect(patch).not.toHaveProperty("mode");
    expect(patch).not.toHaveProperty("atlas");
    expect(patch).not.toHaveProperty("investigate");
    expect(patch).not.toHaveProperty("timeline");
    expect(patch).not.toHaveProperty("flows");
  });

  it("cannot clear selection through a mode-bearing control patch", async () => {
    await act(async () => {
      handle.dispatch({ type: "SELECT_NODE", id: "0xselected" });
      handle.dispatch({ type: "SET_WINDOW", days: 180 });
    });
    await flushSyncDebounce();

    expect(handle.state.selection).toEqual({ nodeId: "0xselected", edgeId: "" });
    expect(callToolMock).not.toHaveBeenCalled();
  });

  it("a visual persistence failure does not roll back data-backed drafts", async () => {
    callToolMock.mockRejectedValueOnce(new Error("save failed"));
    await act(async () => {
      handle.dispatch({ type: "SET_WINDOW", days: 365 });
      handle.dispatch({ type: "SET_LAYOUT", layout: "circular" });
    });
    await flushSyncDebounce();

    expect(handle.syncError).toBe("save failed");
    expect(handle.state.layout).toBe("force");
    expect(handle.state.windowDays).toBe(365);
  });
});
