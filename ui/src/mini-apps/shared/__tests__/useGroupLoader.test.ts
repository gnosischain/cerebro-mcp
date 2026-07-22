// @vitest-environment jsdom

// Tests the shared deferred-group loader without @testing-library: a minimal
// react-dom/client harness renders the hook (under StrictMode, mirroring the
// apps) and exposes its latest return value.

import { act, createElement, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useGroupLoader, type GroupLoader } from "../useGroupLoader";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

interface RecordedCall {
  name: string;
  args: Record<string, unknown>;
  resolve: () => void;
  reject: (err: Error) => void;
}

function makeCallTool() {
  const calls: RecordedCall[] = [];
  const callTool = (name: string, args: Record<string, unknown>) =>
    new Promise<unknown>((resolve, reject) => {
      calls.push({
        name,
        args,
        resolve: () => resolve({}),
        reject: (err: Error) => reject(err),
      });
    });
  return { calls, callTool };
}

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function renderLoader(
  callTool: (name: string, args: Record<string, unknown>) => Promise<unknown>,
  toolName: string,
): { current: () => GroupLoader } {
  const holder: { value: GroupLoader | null } = { value: null };
  function Harness() {
    holder.value = useGroupLoader(callTool, toolName);
    return null;
  }
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root!.render(createElement(StrictMode, null, createElement(Harness)));
  });
  return { current: () => holder.value! };
}

async function settle(action: () => void): Promise<void> {
  await act(async () => {
    action();
    // Drain the .catch/.finally microtask chain before act flushes state.
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  root = null;
  container = null;
});

afterEach(() => {
  if (root) act(() => root!.unmount());
  container?.remove();
});

describe("shared useGroupLoader", () => {
  it("invokes the parameterized toolName with the frozen argument shape", () => {
    const { calls, callTool } = makeCallTool();
    const loader = renderLoader(callTool, "load_governance_datasets");
    act(() => loader.current().sync("view-1", "overview", ["insights"], "scope-a"));
    expect(calls).toHaveLength(1);
    expect(calls[0].name).toBe("load_governance_datasets");
    expect(calls[0].args).toEqual({
      view_id: "view-1", request_id: 0, section: "overview",
      group: "insights", scope_id: "scope-a",
    });
  });

  it("caps concurrent group loads at 2 and drains the queue as calls settle", async () => {
    const { calls, callTool } = makeCallTool();
    const loader = renderLoader(callTool, "load_x_datasets");
    act(() => loader.current().sync("v", "s", ["g1", "g2", "g3"], "scope"));
    expect(calls).toHaveLength(2); // MAX_IN_FLIGHT
    expect(calls.map((c) => c.args.group)).toEqual(["g1", "g2"]);

    await settle(() => calls[0].resolve());
    // The caller re-syncs with the still-unloaded groups (g1 is now loaded
    // in view state, so it is no longer passed).
    act(() => loader.current().sync("v", "s", ["g2", "g3"], "scope"));
    expect(calls).toHaveLength(3);
    expect(calls[2].args.group).toBe("g3");
  });

  it("does not duplicate an in-flight group on repeated sync (StrictMode-safe)", () => {
    const { calls, callTool } = makeCallTool();
    const loader = renderLoader(callTool, "load_x_datasets");
    act(() => loader.current().sync("v", "s", ["g1"], "scope"));
    act(() => loader.current().sync("v", "s", ["g1"], "scope"));
    act(() => loader.current().sync("v", "s", ["g1"], "scope"));
    expect(calls).toHaveLength(1);
  });

  it("remembers failures per scope: no auto-retry under the same scope, fresh scope loads again", async () => {
    const { calls, callTool } = makeCallTool();
    const loader = renderLoader(callTool, "load_x_datasets");
    act(() => loader.current().sync("v", "s", ["g1"], "scope-a"));
    await settle(() => calls[0].reject(new Error("boom")));
    expect(loader.current().failedGroups("scope-a")).toEqual(["s.g1"]);
    expect(loader.current().failedGroups("scope-b")).toEqual([]);

    act(() => loader.current().sync("v", "s", ["g1"], "scope-a"));
    expect(calls).toHaveLength(1); // failure memory blocks the same scope

    act(() => loader.current().sync("v", "s", ["g1"], "scope-b"));
    expect(calls).toHaveLength(2); // a new scope is not poisoned
    expect(calls[1].args.scope_id).toBe("scope-b");
  });

  it("retry clears one failure marker so the next sync re-queues it", async () => {
    const { calls, callTool } = makeCallTool();
    const loader = renderLoader(callTool, "load_x_datasets");
    act(() => loader.current().sync("v", "s", ["g1"], "scope-a"));
    await settle(() => calls[0].reject(new Error("boom")));

    act(() => loader.current().retry("s", "g1", "scope-a"));
    expect(loader.current().failedGroups("scope-a")).toEqual([]);
    act(() => loader.current().sync("v", "s", ["g1"], "scope-a"));
    expect(calls).toHaveLength(2);
  });

  it("retryAll clears every marker for one scope only", async () => {
    const { calls, callTool } = makeCallTool();
    const loader = renderLoader(callTool, "load_x_datasets");
    act(() => loader.current().sync("v", "s", ["g1", "g2"], "scope-a"));
    await settle(() => calls[0].reject(new Error("a")));
    await settle(() => calls[1].reject(new Error("b")));
    act(() => loader.current().sync("v", "other", ["g9"], "scope-z"));
    await settle(() => calls[2].reject(new Error("z")));
    expect(loader.current().failedGroups("scope-a").sort()).toEqual(["s.g1", "s.g2"]);

    act(() => loader.current().retryAll("scope-a"));
    expect(loader.current().failedGroups("scope-a")).toEqual([]);
    expect(loader.current().failedGroups("scope-z")).toEqual(["other.g9"]);
  });

  it("bumps tick on every settle (success and failure)", async () => {
    const { calls, callTool } = makeCallTool();
    const loader = renderLoader(callTool, "load_x_datasets");
    const t0 = loader.current().tick;
    act(() => loader.current().sync("v", "s", ["g1", "g2"], "scope-a"));
    await settle(() => calls[0].resolve());
    const t1 = loader.current().tick;
    expect(t1).toBeGreaterThan(t0);
    await settle(() => calls[1].reject(new Error("boom")));
    expect(loader.current().tick).toBeGreaterThan(t1);
  });
});
