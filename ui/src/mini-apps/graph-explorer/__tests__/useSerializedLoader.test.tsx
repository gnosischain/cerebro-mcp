// @vitest-environment jsdom
// Latest-snapshot-wins loader: never concurrent, and a queued follow-up
// carries the COMPLETE newest snapshot (no stale-closure partial merges).

import { describe, expect, it, beforeEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { useSerializedLoader, type SerializedLoader } from "../state/useSerializedLoader";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

interface Req {
  snapshot: Record<string, unknown>;
  resolve: () => void;
  reject: (err: unknown) => void;
}

let sent: Req[] = [];
let handle: SerializedLoader<Record<string, unknown>>;

function Harness() {
  handle = useSerializedLoader<Record<string, unknown>>(
    (snapshot) =>
      new Promise<void>((resolve, reject) => {
        sent.push({ snapshot, resolve, reject });
      }),
  );
  return null;
}

let root: Root;

beforeEach(async () => {
  sent = [];
  const el = document.createElement("div");
  document.body.appendChild(el);
  root = createRoot(el);
  await act(async () => {
    root.render(<Harness />);
  });
});

describe("useSerializedLoader", () => {
  it("three rapid enqueues => two fetches, second carries the newest full snapshot", async () => {
    let ids: number[] = [];
    await act(async () => {
      ids = [
        handle.enqueue({ grain: "week", range: 365 }), // fires
        handle.enqueue({ grain: "month", range: 365 }), // queued
        handle.enqueue({ grain: "month", range: 90 }), // replaces queued
      ];
    });
    expect(ids).toEqual([1, 2, 3]);
    expect(sent).toHaveLength(1);
    expect(sent[0].snapshot).toEqual({
      grain: "week",
      range: 365,
      request_id: 1,
    });
    expect(handle.loading).toBe(true);
    expect(handle.activeRequestId).toBe(1);
    // Desired advances at enqueue time even though request 3 is queued.
    expect(handle.desiredRequestId).toBe(3);

    await act(async () => {
      sent[0].resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(sent).toHaveLength(2);
    // The queued request is the COMPLETE newest snapshot — never the old
    // grain paired with the new range.
    expect(sent[1].snapshot).toEqual({
      grain: "month",
      range: 90,
      request_id: 3,
    });
    expect(handle.activeRequestId).toBe(3);

    await act(async () => {
      sent[1].resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(sent).toHaveLength(2);
    expect(handle.loading).toBe(false);
    expect(handle.activeRequestId).toBeNull();
    expect(handle.error).toBeNull();
  });

  it("errors drain the queue and clear loading", async () => {
    let failed = 0;
    await act(async () => {
      root.render(<></>);
    });
    const el = document.createElement("div");
    document.body.appendChild(el);
    root = createRoot(el);
    function ErrHarness() {
      handle = useSerializedLoader<Record<string, unknown>>(
        () => Promise.reject(new Error("boom")),
        () => {
          failed += 1;
        },
      );
      return null;
    }
    await act(async () => {
      root.render(<ErrHarness />);
    });
    await act(async () => {
      handle.enqueue({ a: 1 });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(failed).toBe(1);
    expect(handle.loading).toBe(false);
    expect(handle.activeRequestId).toBeNull();
    expect(handle.desiredRequestId).toBe(1);
    expect(handle.error).toBe("boom");
  });

  it("overwrites any caller request_id and starts from a seeded revision", async () => {
    await act(async () => {
      root.render(<></>);
    });
    const el = document.createElement("div");
    document.body.appendChild(el);
    root = createRoot(el);
    function SeededHarness() {
      handle = useSerializedLoader<Record<string, unknown>>(
        (snapshot) =>
          new Promise<void>((resolve, reject) => {
            sent.push({ snapshot, resolve, reject });
          }),
        undefined,
        40,
      );
      return null;
    }
    await act(async () => {
      root.render(<SeededHarness />);
    });
    let id = 0;
    await act(async () => {
      id = handle.enqueue({ request_id: 999, value: "safe" });
    });
    expect(id).toBe(41);
    expect(sent[0].snapshot).toEqual({ request_id: 41, value: "safe" });
  });
});
