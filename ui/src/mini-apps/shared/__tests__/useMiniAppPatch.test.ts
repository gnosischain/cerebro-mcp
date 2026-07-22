import { describe, expect, it } from "vitest";
import type { DatasetDescriptor, MiniAppPayload } from "../miniAppTypes";
import { applyMiniAppPatch } from "../useMiniApp";

interface State {
  dataset_revisions: Record<string, number>;
  transactions: { cursor: string; hashes: string[] };
}

function descriptor(
  rows: unknown[][],
  rowCount = rows.length,
  pageToken: string | null = null,
): DatasetDescriptor {
  return {
    key: "tx_list",
    title: "Transactions",
    sql: "-- in process",
    database: "dbt",
    columns: [{ name: "tx_hash", type: "String" }],
    stats: {
      row_count: rowCount,
      rows_returned: rowCount,
      mode: "exact_bounded",
      warnings: [],
    },
    preview_rows: rows,
    page_token: pageToken,
  };
}

function initial(revision = 4): MiniAppPayload<State> {
  return {
    type: "INITIAL_LOAD",
    view_id: "view-1",
    app_id: "graph_explorer",
    title: "Graph Explorer",
    datasets: { tx_list: descriptor([["0xnewest"]]) },
    view_state: {
      dataset_revisions: { tx_list: revision },
      transactions: { cursor: "first", hashes: ["0xnewest"] },
    },
  };
}

function appendPatch(baseRevision = 4, targetRevision = 5) {
  return {
    view_state: {
      dataset_revisions: { tx_list: targetRevision },
      transactions: {
        cursor: "second",
        hashes: ["0xnewest", "0xolder"],
      },
    },
    dataset_deltas: {
      tx_list: {
        operation: "append" as const,
        base_revision: baseRevision,
        dataset_revision: targetRevision,
        base_row_count: 1,
        rows: [["0xolder"]],
        fallback: descriptor([], 2, "offset:0"),
      },
    },
  };
}

describe("revision-safe mini-app dataset append patches", () => {
  it("appends when both the base revision and materialised base rows match", () => {
    const next = applyMiniAppPatch(initial(), appendPatch());

    expect(next.datasets?.tx_list.preview_rows).toEqual([
      ["0xnewest"],
      ["0xolder"],
    ]);
    expect(next.datasets?.tx_list.page_token).toBeNull();
    expect(next.view_state!.dataset_revisions.tx_list).toBe(5);
    expect(next.view_state!.transactions.cursor).toBe("second");
  });

  it("falls back to a complete refetch when the declared base is unavailable", () => {
    const missingBase = initial(3);
    const next = applyMiniAppPatch(missingBase, appendPatch(4, 5));

    expect(next.datasets?.tx_list.preview_rows).toEqual([]);
    expect(next.datasets?.tx_list.page_token).toBe("offset:0");
    expect(next.datasets?.tx_list.stats.row_count).toBe(2);
    expect(next.view_state!.dataset_revisions.tx_list).toBe(5);
  });

  it("falls back when the revision matches but its rows are not materialised", () => {
    const partiallyMaterialised = initial();
    partiallyMaterialised.datasets = {
      tx_list: descriptor([], 1, "offset:0"),
    };
    const next = applyMiniAppPatch(partiallyMaterialised, appendPatch());

    expect(next.datasets?.tx_list.preview_rows).toEqual([]);
    expect(next.datasets?.tx_list.page_token).toBe("offset:0");
  });

  it("ignores duplicate or out-of-order deltas without regressing state", () => {
    const accepted = initial(5);
    accepted.view_state!.transactions = {
      cursor: "accepted",
      hashes: ["0xnewest", "0xolder"],
    };
    accepted.datasets = {
      tx_list: descriptor([["0xnewest"], ["0xolder"]]),
    };
    const next = applyMiniAppPatch(accepted, appendPatch(4, 5));

    expect(next).toBe(accepted);
    expect(next.view_state!.transactions.cursor).toBe("accepted");
    expect(next.datasets?.tx_list.preview_rows).toHaveLength(2);
  });
});
