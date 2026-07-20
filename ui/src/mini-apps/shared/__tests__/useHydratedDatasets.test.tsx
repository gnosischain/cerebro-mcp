// @vitest-environment jsdom
// Per-key hydration lifecycle tests: geometric publication, per-key
// independence (one key's revision bump must not reset another), and
// stale-page drop after a mid-flight identity change.

import { describe, expect, it, beforeEach } from "vitest";
import { StrictMode, act } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  useHydratedDatasets,
  type HydratedDataset,
  type HydrationPublish,
} from "../useHydratedDatasets";
import type { DatasetDescriptor, PageRowsResponse } from "../miniAppTypes";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

// ---- controllable fetch -----------------------------------------------------

interface PendingFetch {
  key: string;
  token: string;
  resolve: (page: PageRowsResponse | null) => void;
  reject: (err: unknown) => void;
}

let pending: PendingFetch[] = [];

function fetchRows(
  _viewId: string,
  key: string,
  token = "",
): Promise<PageRowsResponse | null> {
  return new Promise((resolve, reject) => {
    pending.push({ key, token, resolve, reject });
  });
}

function page(key: string, rows: unknown[][], next: string): PageRowsResponse {
  return {
    view_id: "v1",
    dataset_key: key,
    columns: ["c"],
    column_types: ["String"],
    rows,
    next_page_token: next,
    total_rows: 0,
    stats: { row_count: 0, rows_returned: rows.length, mode: "exact_bounded", warnings: [] },
  };
}

/** Resolve the oldest pending fetch for `key` with `count` rows. */
async function servePage(key: string, count: number, next: string) {
  const idx = pending.findIndex((p) => p.key === key);
  expect(idx).toBeGreaterThanOrEqual(0);
  const [req] = pending.splice(idx, 1);
  await act(async () => {
    req.resolve(page(key, Array.from({ length: count }, (_, i) => [i]), next));
    // let the loop's awaits settle
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function failPage(key: string, error: Error) {
  const idx = pending.findIndex((p) => p.key === key);
  expect(idx).toBeGreaterThanOrEqual(0);
  const [req] = pending.splice(idx, 1);
  await act(async () => {
    req.reject(error);
    await Promise.resolve();
    await Promise.resolve();
  });
}

function descriptor(key: string, previewLen: number, total: number): DatasetDescriptor {
  return {
    key,
    title: key,
    sql: "",
    database: "dbt",
    columns: [{ name: "c", type: "String" }],
    stats: { row_count: total, rows_returned: previewLen, mode: "exact_bounded", warnings: [] },
    preview_rows: Array.from({ length: previewLen }, (_, i) => [i]),
    page_token: previewLen < total ? `offset:${previewLen}` : "",
  };
}

// ---- harness ----------------------------------------------------------------

let latest: Record<string, HydratedDataset> = {};
let rowIdentities: Map<string, Set<unknown>>;

function Harness({
  descriptors,
  revisions,
  publish,
}: {
  descriptors: Record<string, DatasetDescriptor>;
  revisions: Record<string, number>;
  publish: HydrationPublish;
}) {
  latest = useHydratedDatasets("v1", descriptors, revisions, fetchRows, 100_000, publish);
  for (const [k, d] of Object.entries(latest)) {
    if (!rowIdentities.has(k)) rowIdentities.set(k, new Set());
    rowIdentities.get(k)!.add(d.rows);
  }
  return null;
}

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  pending = [];
  latest = {};
  rowIdentities = new Map();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

async function render(
  descriptors: Record<string, DatasetDescriptor>,
  revisions: Record<string, number>,
  publish: HydrationPublish = "geometric",
) {
  await act(async () => {
    root.render(
      <Harness descriptors={descriptors} revisions={revisions} publish={publish} />,
    );
  });
}

// ---- tests ------------------------------------------------------------------

describe("useHydratedDatasets (per-key + geometric)", () => {
  it("geometric publication: doubles between publishes, always flushes final", async () => {
    await render({ a: descriptor("a", 500, 3000) }, { a: 1 });
    // pages of 500 rows: 500 -> 1000 -> 1500 -> 2000 -> 2500 -> 3000
    await servePage("a", 500, "offset:1000"); // 1000  publish (first page)
    await servePage("a", 500, "offset:1500"); // 1500  withheld (< 2000)
    await servePage("a", 500, "offset:2000"); // 2000  publish (doubled)
    await servePage("a", 500, "offset:2500"); // 2500  withheld
    await servePage("a", 500, "");            // 3000  completion flush
    expect(latest.a.rows.length).toBe(3000);
    expect(latest.a.hydrating).toBe(false);
    expect(latest.a.phase).toBe("complete");
    expect(latest.a.rowsLoaded).toBe(3000);
    expect(latest.a.rowsExpected).toBe(3000);
    expect(latest.a.error).toBeNull();
    expect(latest.a.truncated).toBe(false);
    // distinct row-array identities: preview(500), 1000, 2000, 3000 = 4
    expect(rowIdentities.get("a")!.size).toBe(4);
  });

  it("bumping one key's revision does not reset the other key", async () => {
    const descs = {
      a: descriptor("a", 500, 1000),
      b: descriptor("b", 500, 1000),
    };
    await render(descs, { a: 1, b: 1 });
    await servePage("a", 500, "");
    await servePage("b", 500, "");
    expect(latest.a.rows.length).toBe(1000);
    expect(latest.b.rows.length).toBe(1000);
    const bRows = latest.b.rows;

    // Revision bump on A only.
    await render(descs, { a: 2, b: 1 });
    // B is untouched: same array identity, no re-hydration request queued.
    expect(latest.b.rows).toBe(bRows);
    expect(pending.filter((p) => p.key === "b")).toHaveLength(0);
    // A restarted from its preview and refetches.
    expect(latest.a.rows.length).toBe(500);
    await servePage("a", 500, "");
    expect(latest.a.rows.length).toBe(1000);
  });

  it("hydrates exactly once under StrictMode (dev double-mount)", async () => {
    // StrictMode mounts, simulates unmount (registry cleared), remounts.
    // The remount's loops must be the only live ones — the first mount's
    // loop dies by token mismatch, and pages are NOT double-applied.
    await act(async () => {
      root.render(
        <StrictMode>
          <Harness
            descriptors={{ a: descriptor("a", 500, 1500) }}
            revisions={{ a: 1 }}
            publish="every-page"
          />
        </StrictMode>,
      );
    });
    // Both mount cycles may have issued a first fetch; the stale one's
    // response must be dropped. Serve every pending request.
    while (pending.some((p) => p.key === "a")) {
      const req = pending.splice(pending.findIndex((p) => p.key === "a"), 1)[0];
      const next = req.token === "offset:500" ? "offset:1000" : "";
      await act(async () => {
        req.resolve(page("a", Array.from({ length: 500 }, (_, i) => [i]), next));
        await Promise.resolve();
        await Promise.resolve();
      });
    }
    expect(latest.a.rows.length).toBe(1500); // once, not doubled
    expect(latest.a.hydrating).toBe(false);
    expect(latest.a.phase).toBe("complete");
  });

  it("drops stale pages after a mid-flight identity change", async () => {
    await render({ a: descriptor("a", 500, 2000) }, { a: 1 });
    // Capture the in-flight request from the FIRST loop, do not resolve yet.
    expect(pending.filter((p) => p.key === "a")).toHaveLength(1);
    const staleReq = pending.splice(0, 1)[0];

    // Identity changes mid-flight (revision bump) -> new loop starts.
    await render({ a: descriptor("a", 500, 2000) }, { a: 2 });
    expect(pending.filter((p) => p.key === "a")).toHaveLength(1);

    // The OLD loop's page resolves late — it must NOT be applied.
    await act(async () => {
      staleReq.resolve(page("a", Array.from({ length: 500 }, (_, i) => ["stale", i]), "offset:9999"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(latest.a.rows.length).toBe(500); // still the fresh preview
    expect(pending.filter((p) => p.key === "a" && p.token === "offset:9999")).toHaveLength(0);

    // The NEW loop completes normally.
    await servePage("a", 500, "");
    expect(latest.a.rows.length).toBe(1000);
    expect(latest.a.hydrating).toBe(false);
  });

  it("retains partial rows and exposes an explicit failed phase", async () => {
    await render({ a: descriptor("a", 500, 2000) }, { a: 1 }, "every-page");
    expect(latest.a.phase).toBe("loading");
    expect(latest.a.rowsLoaded).toBe(500);
    expect(latest.a.rowsExpected).toBe(2000);

    await servePage("a", 500, "offset:1000");
    expect(latest.a.rows.length).toBe(1000);
    await failPage("a", new Error("bridge unavailable"));

    expect(latest.a.rows.length).toBe(1000);
    expect(latest.a.rowsLoaded).toBe(1000);
    expect(latest.a.rowsExpected).toBe(2000);
    expect(latest.a.phase).toBe("failed");
    expect(latest.a.hydrating).toBe(false);
    expect(latest.a.error).toBe("bridge unavailable");
  });

  it("does not turn a null page into a successful completion", async () => {
    await render({ a: descriptor("a", 10, 20) }, { a: 1 });
    const req = pending.shift();
    expect(req).toBeDefined();
    await act(async () => {
      req!.resolve(null);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(latest.a.phase).toBe("failed");
    expect(latest.a.rowsLoaded).toBe(10);
    expect(latest.a.error).toContain("No hydration page returned");
  });

  it("fails when a descriptor advertises unseen rows without a page token", async () => {
    const broken = descriptor("a", 10, 20);
    broken.page_token = "";
    await render({ a: broken }, { a: 1 });

    expect(pending).toHaveLength(0);
    expect(latest.a.rows).toHaveLength(10);
    expect(latest.a.phase).toBe("failed");
    expect(latest.a.hydrating).toBe(false);
    expect(latest.a.error).toContain("Hydration token missing");
  });

  it("fails when a continuation chain ends before the advertised total", async () => {
    await render({ a: descriptor("a", 500, 2000) }, { a: 1 }, "every-page");
    await servePage("a", 500, "");

    expect(latest.a.rows).toHaveLength(1000);
    expect(latest.a.rowsExpected).toBe(2000);
    expect(latest.a.phase).toBe("failed");
    expect(latest.a.hydrating).toBe(false);
    expect(latest.a.error).toContain("Hydration ended early");
  });
});
