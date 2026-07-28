// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HydratedDataset } from "../../shared/useHydratedDatasets";
import {
  TransactionsView,
  resetTransactionTaskUiForTests,
} from "../modes/TransactionsView";
import { buildInitialState } from "../state/graphReducer";
import type { GraphExplorerViewState } from "../types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const HASH_A = `0x${"aa".repeat(32)}`;
const HASH_B = `0x${"bb".repeat(32)}`;
const HASH_C = `0x${"cc".repeat(32)}`;
const SOURCE = `0x${"11".repeat(20)}`;
const TARGET = `0x${"22".repeat(20)}`;
const TOKEN = `0x${"33".repeat(20)}`;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  resetTransactionTaskUiForTests();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  resetTransactionTaskUiForTests();
});

function hydrated(rows: unknown[][]): HydratedDataset {
  return {
    rows,
    columns: [],
    columnTypes: [],
    phase: "complete",
    rowsLoaded: rows.length,
    rowsExpected: rows.length,
    error: null,
    hydrating: false,
    truncated: false,
  };
}

function txLeg(hash: string, rank: number): unknown[] {
  return [
    `leg:${hash}:1`,
    SOURCE,
    TARGET,
    hash,
    1,
    47_000_000 + rank,
    rank,
    "2026-07-19 12:00:00",
    TOKEN,
    "TOK",
    1,
    null,
    rank,
    rank,
    "success",
    "1000000000000000000",
  ];
}

function serverFor(
  hashes: string[],
  rangeDays = 30,
  maxTxs = 25,
  seed = "",
): GraphExplorerViewState {
  return {
    mode: "transactions",
    transactions: {
      tx_hashes: hashes,
      seed,
      counterparties: [],
      tokens: [],
      range_days: rangeDays,
      max_txs: maxTxs,
      scope: {
        scope_id: `tx:test:${hashes.join(":") || "empty"}`,
        request_id: 1,
        status: "ready",
        data_horizon: 47_000_000 + hashes.length,
        result_observed_through: "2026-07-19 13:00:00",
        window: {
          t0: seed ? null : "2026-07-19 00:00:00",
          t1: seed ? null : "2026-07-19 13:00:00",
          source: seed ? "execution_tables_plus_rpc_head" : "ignored_for_explicit_hash",
        },
        coverage: {
          rows: { shown: hashes.length, total: hashes.length },
          usd: { known: 0, total: null, unknown_rows: hashes.length },
        },
        truncation: { truncated: false },
        verification: { status: "verified" },
        query_kind: seed
          ? "address_discovery"
          : hashes.length
            ? "explicit_hash"
            : "",
        discovery_path: seed ? "execution_tables_rpc_tail" : "rpc_receipt",
        sources: [],
        residuals: [],
        warnings: [],
        receipt_statuses: Object.fromEntries(hashes.map((hash) => [hash, "success"])),
      },
    },
    selection: { node_id: "", edge_id: "" },
  } as unknown as GraphExplorerViewState;
}

function renderTransactions(
  server: GraphExplorerViewState,
  requestTransactions = vi.fn(),
  viewId = "view-a",
  loadError: string | null = null,
  options: {
    legRows?: unknown[][];
    nodeRows?: unknown[][];
    txLegsDataset?: HydratedDataset;
    txListRows?: unknown[][];
    onSelectNode?: (id: string) => void;
    onSelectEdge?: (id: string) => void;
    onClearSelection?: () => void;
    loading?: boolean;
  } = {},
) {
  const hashes = server.transactions?.tx_hashes ?? [];
  const txLegs = options.txLegsDataset ?? hydrated(
    options.legRows ?? hashes.map((hash, index) => txLeg(hash, index)),
  );
  const txNodes = hydrated(
    options.nodeRows ?? [
      [SOURCE, "source", "address", "", 0, 0, 0, hashes.length, []],
      [TARGET, "target", "address", "", 0, 0, 0, hashes.length, []],
    ],
  );
  root.render(
    <TransactionsView
      viewId={viewId}
      server={server}
      local={buildInitialState(server)}
      txNodes={txNodes}
      txLegs={txLegs}
      txList={hydrated(options.txListRows ?? [])}
      nodeEvidence={undefined}
      edgeEvidence={undefined}
      evidenceExpectation={null}
      requestTransactions={requestTransactions}
      loading={options.loading ?? false}
      loadError={loadError}
      onSelectNode={options.onSelectNode ?? vi.fn()}
      onSelectEdge={options.onSelectEdge ?? vi.fn()}
      onClearSelection={options.onClearSelection ?? vi.fn()}
    />,
  );
  return requestTransactions;
}

describe("TransactionsView task persistence", () => {
  it("submits explicit UTC, activity, and token filters as one discovery snapshot", async () => {
    const requestTransactions = vi.fn();
    await act(async () => renderTransactions(serverFor([]), requestTransactions));

    await act(async () => {
      container.querySelectorAll<HTMLButtonElement>(".ge-tx-query-kind button")[1]?.click();
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent?.includes("Filters"))
        ?.click();
    });

    const setInput = (input: HTMLInputElement | null, value: string) => {
      const setValue = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      setValue?.call(input, value);
      input?.dispatchEvent(new Event("input", { bubbles: true }));
      input?.dispatchEvent(new Event("change", { bubbles: true }));
    };

    await act(async () => {
      setInput(
        container.querySelector<HTMLInputElement>("input[aria-label='Token address filter']"),
        TOKEN,
      );
      container.querySelector<HTMLInputElement>("input[type=radio][value=custom]")?.click();
    });
    await act(async () => {
      setInput(
        container.querySelector<HTMLInputElement>("input[aria-label='From UTC']"),
        "2026-07-01T00:00",
      );
      setInput(
        container.querySelector<HTMLInputElement>("input[aria-label='To UTC']"),
        "2026-07-19T12:30",
      );
      setInput(container.querySelector<HTMLInputElement>("input.ge-tx-input"), SOURCE);
    });
    await act(async () => {
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "Search activity →")
        ?.click();
    });

    expect(requestTransactions).toHaveBeenCalledTimes(1);
    expect(requestTransactions).toHaveBeenCalledWith(expect.objectContaining({
      operation: "discover",
      seed: SOURCE,
      txHashes: [],
      tokens: [TOKEN],
      activityKinds: ["erc20"],
      t0: "2026-07-01T00:00:00.000Z",
      t1: "2026-07-19T12:30:00.000Z",
      rangeDays: 0,
      cursor: "",
    }));
  });

  it("requires a paired, ordered UTC window before address discovery", async () => {
    await act(async () => renderTransactions(serverFor([])));
    await act(async () => {
      container.querySelectorAll<HTMLButtonElement>(".ge-tx-query-kind button")[1]?.click();
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent?.includes("Filters"))
        ?.click();
    });
    await act(async () => {
      container.querySelector<HTMLInputElement>("input[type=radio][value=custom]")?.click();
    });
    expect(container.textContent).toContain("Enter both UTC bounds");
    expect(
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "Search activity →")
        ?.disabled,
    ).toBe(true);
  });

  it("restores selected receipt, pending controls, canvas, and details after remount", async () => {
    const server = serverFor([HASH_A, HASH_B], 30, 25, SOURCE);
    const requestTransactions = vi.fn();
    await act(async () => renderTransactions(server, requestTransactions));

    const selects = container.querySelectorAll<HTMLSelectElement>("select");
    await act(async () => {
      container.querySelector<HTMLButtonElement>(`button[title="${HASH_B}"]`)?.click();
      selects[0].value = "50";
      selects[0].dispatchEvent(new Event("change", { bubbles: true }));
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "Show transfer graph")
        ?.click();
      container.querySelector<HTMLButtonElement>("button[title='Hide details']")?.click();
    });

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => renderTransactions(server, requestTransactions));

    const restoredSelects = container.querySelectorAll<HTMLSelectElement>("select");
    expect(restoredSelects).toHaveLength(1);
    expect(restoredSelects[0].value).toBe("50");
    expect(
      container.querySelector(`button[title="${HASH_B}"]`)?.classList.contains("active"),
    ).toBe(true);
    expect(container.textContent).toContain("Hide transfer graph");
    expect(
      container.querySelector("button[title='Show details']")?.getAttribute("aria-pressed"),
    ).toBe("false");
    expect(requestTransactions).toHaveBeenCalledWith(expect.objectContaining({
      operation: "discover",
      txHashes: [],
      seed: SOURCE,
      counterparties: [],
      tokens: [],
      minUsd: 0,
      t0: "",
      t1: "",
      maxTxs: 50,
      pageSize: 50,
      cursor: "",
    }));
  });

  it("resets cached UI only when the loaded transaction subject changes", async () => {
    await act(async () => renderTransactions(serverFor([HASH_A, HASH_B], 30, 25, SOURCE)));
    const selects = container.querySelectorAll<HTMLSelectElement>("select");
    await act(async () => {
      container.querySelector<HTMLButtonElement>(`button[title="${HASH_B}"]`)?.click();
      selects[0].value = "50";
      selects[0].dispatchEvent(new Event("change", { bubbles: true }));
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "Show transfer graph")
        ?.click();
    });

    await act(async () => renderTransactions(serverFor([HASH_C], 7, 5)));

    const resetSelects = container.querySelectorAll<HTMLSelectElement>("select");
    expect(resetSelects).toHaveLength(0);
    expect(container.textContent).not.toContain("Address lookback");
    expect(container.textContent).toContain("Show transfer graph");
    expect(container.querySelector(`code[title="${HASH_C}"]`)).not.toBeNull();
  });

  it("uses all-history address discovery and retries discovery as discovery", async () => {
    const requestTransactions = vi.fn();
    await act(async () =>
      renderTransactions(serverFor([HASH_A]), requestTransactions),
    );
    expect(container.textContent).not.toContain("Address lookback");

    const discovery = serverFor([HASH_A, HASH_B], 30, 25, SOURCE);
    await act(async () =>
      renderTransactions(discovery, requestTransactions, "view-a", "timed out"),
    );
    expect(container.textContent).toContain("Filters");
    await act(async () =>
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "Retry")
        ?.click(),
    );
    expect(requestTransactions).toHaveBeenLastCalledWith(expect.objectContaining({
      operation: "discover",
      txHashes: [],
      seed: SOURCE,
      counterparties: [],
      tokens: [],
      rangeDays: 0,
      t0: "",
      t1: "",
      maxTxs: 25,
    }));
  });

  it("keeps the exact Money Trail window when only Txs changes", async () => {
    const exact = serverFor([HASH_A, HASH_B], 30, 25, SOURCE);
    if (exact.transactions?.scope) {
      exact.transactions.scope.window.source = "money_trail_applied_window";
    }
    if (exact.transactions) {
      exact.transactions.t0 = "2026-07-01 00:00:00";
      exact.transactions.t1 = "2026-07-19 00:00:00";
    }
    const requestTransactions = vi.fn();
    await act(async () => renderTransactions(exact, requestTransactions));
    const txs = container.querySelectorAll<HTMLSelectElement>("select")[0];
    await act(async () => {
      txs.value = "50";
      txs.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(requestTransactions).toHaveBeenLastCalledWith(expect.objectContaining({
      operation: "discover",
      txHashes: [],
      seed: SOURCE,
      counterparties: [],
      tokens: [],
      minUsd: 0,
      t0: "2026-07-01T00:00:00.000Z",
      t1: "2026-07-19T00:00:00.000Z",
      maxTxs: 50,
      pageSize: 50,
      cursor: "",
    }));
  });

  it("submits a pasted explicit hash exactly once", async () => {
    const requestTransactions = vi.fn();
    await act(async () => renderTransactions(serverFor([]), requestTransactions));
    const input = container.querySelector<HTMLInputElement>("input.ge-tx-input");
    await act(async () => {
      if (!input) return;
      const setValue = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      setValue?.call(input, HASH_A);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () =>
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent?.includes("Open"))
        ?.click(),
    );
    expect(requestTransactions).toHaveBeenCalledTimes(1);
    expect(requestTransactions).toHaveBeenCalledWith({
      operation: "receipt",
      txHashes: [HASH_A],
      seed: "",
      // Every request now stamps the chain; asserting it explicitly locks the
      // Gnosis default rather than merely tolerating the new field.
      chain: "100",
    });
  });

  it("discloses receipt decode failures and never labels them complete", async () => {
    const server = serverFor([HASH_A]);
    const scope = server.transactions?.scope;
    if (scope) {
      scope.status = "partial";
      scope.verification.status = "unverified";
      scope.coverage.rows = { shown: 1, total: 2 };
      scope.decode_failures = [
        {
          transaction_hash: HASH_A,
          log_index: 8,
          error: "data is not a 32-byte uint256 ABI word",
        },
      ];
    }
    await act(async () => renderTransactions(server));

    expect(container.querySelector(".ge-evidence-trigger")?.getAttribute("aria-label")).toContain(
      "PARTIAL RECEIPT INSPECTION",
    );
    expect(container.textContent).toContain("1 matching Transfer log");
    expect(container.textContent).toContain("No zero amount was invented");
  });

  it("uses transaction and transfer-leg contexts without semantic edge evidence", async () => {
    const onSelectEdge = vi.fn();
    const onSelectNode = vi.fn();
    await act(async () =>
      renderTransactions(serverFor([HASH_A]), vi.fn(), "view-a", null, {
        onSelectEdge,
        onSelectNode,
      }),
    );

    const inspect = container.querySelector<HTMLButtonElement>(
      "button[aria-label^='Inspect transfer']",
    );
    await act(async () => inspect?.click());

    const inspector = container.querySelector<HTMLElement>(
      "aside[aria-label='Transaction details']",
    );
    expect(inspector?.querySelector("[data-inspector-context=transaction]")).not.toBeNull();
    expect(inspector?.querySelector("[data-inspector-context=transfer-leg]")).not.toBeNull();
    expect(inspector?.textContent).toContain("Transfer leg");
    expect(inspector?.textContent).toContain("USDunknown");
    expect(inspector?.textContent).not.toContain("edge_count");
    expect(inspector?.textContent).not.toContain("profile");
    expect(
      inspector?.querySelector<HTMLAnchorElement>(
        `a[href="https://gnosis.blockscout.com/tx/${HASH_A}"]`,
      ),
    ).not.toBeNull();
    expect(onSelectEdge).not.toHaveBeenCalled();
    expect(onSelectNode).not.toHaveBeenCalled();
  });

  it("keeps selected rows, arcs, and participants aligned without requiring the graph", async () => {
    const onSelectEdge = vi.fn();
    const onSelectNode = vi.fn();
    await act(async () =>
      renderTransactions(serverFor([HASH_A]), vi.fn(), "view-a", null, {
        onSelectEdge,
        onSelectNode,
      }),
    );

    const hideGraph = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "Hide transfer graph");
    await act(async () => hideGraph?.click());
    expect(container.querySelector("svg[aria-label^='Transfer graph']")).toBeNull();

    const row = container.querySelector<HTMLTableRowElement>(".ge-tx-table tbody tr");
    const inspect = row?.querySelector<HTMLButtonElement>(
      "button[aria-label^='Inspect transfer']",
    );
    await act(async () => inspect?.click());
    expect(row?.classList.contains("is-selected")).toBe(true);
    const showSelected = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "Show in graph");
    await act(async () => showSelected?.click());
    expect(
      container.querySelector(".tx-svg-hit[aria-pressed=true]"),
    ).not.toBeNull();

    const sender = container.querySelector<HTMLButtonElement>(
      `.ge-tx-addr[title^="${SOURCE}"]`,
    );
    await act(async () => sender?.click());
    expect(
      container.querySelector("[data-inspector-context=participant]"),
    ).not.toBeNull();
    expect(
      container.querySelector(`[data-node-id="${SOURCE}"][aria-pressed=true]`),
    ).not.toBeNull();
    expect(container.querySelector(".tx-svg-hit[aria-pressed=true]")).toBeNull();
    expect(onSelectEdge).not.toHaveBeenCalled();
    expect(onSelectNode).not.toHaveBeenCalled();
  });

  it("makes ordered transfers keyboard selectable through a real button", async () => {
    await act(async () => renderTransactions(serverFor([HASH_A])));
    const row = container.querySelector<HTMLTableRowElement>("tbody tr");
    expect(row?.tabIndex).toBe(-1);
    const inspect = row?.querySelector<HTMLButtonElement>(
      "button[aria-label^='Inspect transfer']",
    );
    expect(inspect?.tabIndex).toBe(0);
    await act(async () => inspect?.click());
    expect(inspect?.getAttribute("aria-pressed")).toBe("true");
    expect(
      container.querySelector("[data-inspector-context=transfer-leg]"),
    ).not.toBeNull();
  });

  it("uses legacy cursor expansion for Follow without misrouting it to discover", async () => {
    const requestTransactions = vi.fn();
    await act(async () =>
      renderTransactions(serverFor([HASH_A]), requestTransactions),
    );

    const follow = container.querySelector<HTMLButtonElement>(
      `button[aria-label="Follow ${SOURCE} forward"]`,
    );
    await act(async () => follow?.click());

    expect(requestTransactions).toHaveBeenCalledWith(expect.objectContaining({
      operation: "legacy",
      expandNodeId: SOURCE,
      afterBlock: 47_000_000,
      afterIndex: 0,
      merge: true,
    }));
  });

  it("clears a leg from another receipt when the active transaction changes", async () => {
    await act(async () => renderTransactions(serverFor([HASH_A, HASH_B])));
    const inspect = container.querySelector<HTMLButtonElement>(
      ".ge-tx-table button[aria-label^='Inspect transfer']",
    );
    await act(async () => inspect?.click());
    expect(
      container.querySelector(`button[title="${HASH_B}"]`)?.classList.contains("active"),
    ).toBe(true);
    expect(
      container.querySelector("[data-inspector-context=transfer-leg]"),
    ).not.toBeNull();

    await act(async () =>
      container.querySelector<HTMLButtonElement>(`button[title="${HASH_A}"]`)?.click(),
    );
    expect(
      container.querySelector(`button[title="${HASH_A}"]`)?.classList.contains("active"),
    ).toBe(true);
    expect(container.querySelector("[data-inspector-context=transfer-leg]")).toBeNull();
    expect(
      container.querySelector("[data-inspector-context=selection-help]"),
    ).not.toBeNull();
  });

  it("reports a verified zero-leg receipt as one transaction, not zero transactions", async () => {
    const server = serverFor([HASH_A]);
    const scope = server.transactions?.scope;
    if (scope) {
      scope.coverage.rows = { shown: 0, total: 0 };
    }
    await act(async () =>
      renderTransactions(server, vi.fn(), "view-a", null, {
        legRows: [],
        txListRows: [[HASH_A, 12_345, 7, "2026-07-18 12:00:00", 0, 0]],
      }),
    );

    const evidence = container.querySelector<HTMLButtonElement>(".ge-evidence-trigger");
    expect(evidence?.getAttribute("aria-label")).toContain(
      "RPC VERIFIED · 0/0 ERC-20 LEGS",
    );
    await act(async () => evidence?.click());
    expect(container.querySelector(".ge-evidence-panel__details")?.textContent).toContain(
      "0/0 receipt legs · 1 transaction",
    );
    expect(container.textContent).toContain(
      "Receipt verified: this transaction has no ERC-20 Transfer legs.",
    );
  });

  it("surfaces leg hydration failure with retry and never claims completeness", async () => {
    const partial = hydrated([txLeg(HASH_A, 0)]);
    partial.phase = "failed";
    partial.rowsLoaded = 1;
    partial.rowsExpected = 2;
    partial.error = "page 2 unavailable";
    await act(async () =>
      renderTransactions(serverFor([HASH_A]), vi.fn(), "view-a", null, {
        txLegsDataset: partial,
      }),
    );

    expect(container.querySelector(".ge-evidence-trigger")?.getAttribute("aria-label")).toContain(
      "FAILED RECEIPT HYDRATION · 1/2",
    );
    expect(container.querySelector(".ge-load-error")?.textContent).toContain(
      "page 2 unavailable",
    );
    expect(
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .some((button) => button.textContent === "Retry"),
    ).toBe(true);
    expect(container.textContent).not.toContain("COMPLETE");
  });

  it("never describes an empty address search as a zero-leg receipt", async () => {
    const emptyDiscovery = serverFor([], 30, 25, SOURCE);
    const scope = emptyDiscovery.transactions?.scope;
    if (scope) {
      scope.coverage.rows = { shown: 0, total: 0 };
      scope.query_kind = "address_discovery";
      scope.window = { t0: null, t1: null, source: "execution_tables_plus_rpc_head" };
      scope.discovery_path = "execution_tables_rpc_tail";
      scope.discovery_coverage = {
        complete: true,
        total_exact: 0,
        total_lower_bound: 0,
        next_cursor: null,
        scanned_ranges: [],
        uncovered_ranges: [],
        older_history_unscanned: false,
      };
    }
    await act(async () =>
      renderTransactions(emptyDiscovery, vi.fn(), "view-a", null, {
        legRows: [],
        txListRows: [],
      }),
    );

    expect(
      container.querySelector(".ge-evidence-trigger")?.getAttribute("aria-label"),
    ).toContain("NO MATCHES · VERIFIED ADDRESS DISCOVERY");
    expect(container.textContent).toContain(
      "No direct or standard ERC-20 Transfer transactions found",
    );
    expect(container.textContent).toContain("checked through block 47000000");
    expect(container.textContent).not.toContain("Search 90 days");
    expect(container.textContent).not.toContain("Receipt verified: this transaction has no");
    expect(container.textContent).not.toContain("COMPLETE");
  });

  it("renders address-discovery candidates newest first before receipt detail", async () => {
    const discovery = serverFor([HASH_A, HASH_B], 90, 25, SOURCE);
    await act(async () =>
      renderTransactions(discovery, vi.fn(), "view-a", null, {
        txListRows: [
          [HASH_A, 47_000_000, 1, "2026-07-18 10:00:00", 1, 1],
          [HASH_B, 47_000_100, 2, "2026-07-19 10:00:00", 1, 1],
        ],
      }),
    );

    const resultRows = container.querySelectorAll<HTMLTableRowElement>(
      ".ge-tx-discovery-results tbody tr",
    );
    expect(resultRows).toHaveLength(2);
    expect(resultRows[0].textContent).toContain(HASH_B);
    expect(
      resultRows[0].querySelector("button[aria-current]")?.getAttribute("aria-current"),
    ).not.toBe("true");
    expect(container.querySelector(".ge-tx-table-region")).toBeNull();

    await act(async () =>
      resultRows[0].querySelector<HTMLButtonElement>("button")?.click(),
    );

    expect(container.querySelector(".ge-tx-discovery-results")).toBeNull();
    expect(container.querySelector(".ge-tx-table-region")).not.toBeNull();
    expect(container.querySelector(".ge-tx-overview")?.textContent).toContain(
      "Show transfer graph",
    );
    expect(container.querySelector(".ge-tx-receipt-nav")?.textContent).toContain(
      "← Address activity",
    );
    await act(async () =>
      container.querySelector<HTMLButtonElement>(".ge-tx-receipt-nav button")?.click(),
    );
    const restoredRows = container.querySelectorAll<HTMLTableRowElement>(
      ".ge-tx-discovery-results tbody tr",
    );
    expect(
      restoredRows[0]?.querySelector("button[aria-current]")?.getAttribute("aria-current"),
    ).toBe("true");
  });

  it("keeps keyset pagination on the candidate-only discover operation", async () => {
    const requestTransactions = vi.fn();
    const discovery = serverFor([HASH_A], 30, 25, SOURCE);
    const transactions = discovery.transactions as unknown as Record<string, unknown>;
    transactions.discovery_coverage = {
      complete: false,
      total_exact: null,
      total_lower_bound: 1,
      next_cursor: "opaque-next-page",
      scanned_ranges: [],
      uncovered_ranges: [],
      older_history_unscanned: true,
    };
    transactions.query = {
      kind: "address",
      address: SOURCE,
      tokens: [TOKEN],
      counterparties: [],
      activity_kinds: ["erc20"],
      page_size: 25,
    };

    await act(async () =>
      renderTransactions(discovery, requestTransactions, "view-a", null, {
        txListRows: [[HASH_A, 47_000_000, 1, "2026-07-18 10:00:00", 1, 1]],
      }),
    );
    await act(async () =>
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "Continue older history")
        ?.click(),
    );

    expect(requestTransactions).toHaveBeenCalledWith(expect.objectContaining({
      operation: "discover",
      seed: SOURCE,
      txHashes: [],
      tokens: [TOKEN],
      cursor: "opaque-next-page",
      pageSize: 25,
      activityKinds: ["erc20"],
    }));
  });

  it("offers and sends a coverage continuation for a zero-row partial page", async () => {
    const requestTransactions = vi.fn();
    const discovery = serverFor([], 0, 25, SOURCE);
    const transactions = discovery.transactions as unknown as Record<string, unknown>;
    transactions.discovery_coverage = {
      complete: false,
      total_exact: null,
      total_lower_bound: 0,
      next_cursor: "opaque-coverage-cursor",
      scanned_ranges: [
        { t0: "2026-07-12T00:00:00Z", t1: "2026-07-19T01:00:01Z" },
      ],
      uncovered_ranges: [
        {
          t0: "2018-10-08T00:00:00Z",
          t1: "2026-07-12T00:00:00Z",
          reason: "interactive discovery wall-time budget reached",
        },
      ],
      older_history_unscanned: true,
    };
    transactions.query = {
      kind: "address",
      address: SOURCE,
      tokens: [],
      counterparties: [],
      activity_kinds: ["direct", "erc20"],
      page_size: 25,
    };
    const scope = discovery.transactions?.scope;
    if (scope) {
      scope.status = "partial";
      scope.verification = { status: "unverified", method: "partial coverage" };
      scope.discovery_coverage = transactions.discovery_coverage as never;
      scope.more_transactions_available = true;
    }

    await act(async () =>
      renderTransactions(discovery, requestTransactions, "view-a", null, {
        txListRows: [],
        legRows: [],
      }),
    );
    const loadOlder = [...container.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "Continue older history");
    expect(loadOlder).toBeDefined();
    await act(async () => loadOlder?.click());

    expect(requestTransactions).toHaveBeenCalledWith(expect.objectContaining({
      operation: "discover",
      seed: SOURCE,
      txHashes: [],
      cursor: "opaque-coverage-cursor",
      pageSize: 25,
      activityKinds: ["direct", "erc20"],
    }));
  });

  it("automatically advances empty safe slices after an analyst starts an all-history search", async () => {
    const requestTransactions = vi.fn();
    await act(async () => renderTransactions(serverFor([]), requestTransactions));

    const setValue = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set;
    await act(async () => {
      container.querySelectorAll<HTMLButtonElement>(".ge-tx-query-kind button")[1]?.click();
    });
    await act(async () => {
      const addressInput = container.querySelector<HTMLInputElement>("input.ge-tx-input");
      setValue?.call(addressInput, SOURCE);
      addressInput?.dispatchEvent(new Event("input", { bubbles: true }));
      addressInput?.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "Search activity →")
        ?.click();
    });
    expect(requestTransactions).toHaveBeenCalledTimes(1);

    const partial = serverFor([], 0, 25, SOURCE);
    const transactions = partial.transactions as unknown as Record<string, unknown>;
    transactions.query = {
      kind: "address",
      address: SOURCE,
      tokens: [],
      counterparties: [],
      activity_kinds: ["direct", "erc20"],
      page_size: 25,
    };
    transactions.discovery_coverage = {
      complete: false,
      total_exact: null,
      total_lower_bound: 0,
      next_cursor: "automatic-coverage-cursor",
      scanned_ranges: [],
      uncovered_ranges: [{
        t0: "2018-10-08T00:00:00Z",
        t1: "2026-07-12T00:00:00Z",
        reason: "interactive budget reached",
      }],
      older_history_unscanned: true,
    };
    const scope = partial.transactions?.scope;
    if (scope) {
      scope.scope_id = "transactions:2:automatic";
      scope.request_id = 2;
      scope.status = "partial";
      scope.verification = { status: "unverified", method: "partial coverage" };
      scope.discovery_coverage = transactions.discovery_coverage as never;
      scope.more_transactions_available = true;
    }

    await act(async () => renderTransactions(partial, requestTransactions, "view-a", null, {
      txListRows: [],
      legRows: [],
    }));

    expect(requestTransactions).toHaveBeenCalledTimes(2);
    expect(requestTransactions).toHaveBeenLastCalledWith(expect.objectContaining({
      operation: "discover",
      seed: SOURCE,
      cursor: "automatic-coverage-cursor",
      pageSize: 25,
      activityKinds: ["direct", "erc20"],
    }));
    expect(container.textContent).toContain("Searching address history");
    expect(container.textContent).not.toContain("No matches in the scanned portion yet");
  });

  it("continues after early matches until the requested candidate page is full", async () => {
    const requestTransactions = vi.fn();
    await act(async () => renderTransactions(serverFor([]), requestTransactions));

    const setValue = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set;
    await act(async () => {
      container.querySelectorAll<HTMLButtonElement>(".ge-tx-query-kind button")[1]?.click();
    });
    await act(async () => {
      const addressInput = container.querySelector<HTMLInputElement>("input.ge-tx-input");
      setValue?.call(addressInput, SOURCE);
      addressInput?.dispatchEvent(new Event("input", { bubbles: true }));
      addressInput?.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "Search activity →")
        ?.click();
    });
    expect(requestTransactions).toHaveBeenCalledTimes(1);

    const partial = serverFor([HASH_A], 1, 25, SOURCE);
    const transactions = partial.transactions as unknown as Record<string, unknown>;
    transactions.query = {
      kind: "address",
      address: SOURCE,
      tokens: [],
      counterparties: [],
      activity_kinds: ["direct", "erc20"],
      page_size: 25,
    };
    transactions.discovery_coverage = {
      complete: false,
      total_exact: null,
      total_lower_bound: 1,
      next_cursor: "continue-after-first-match",
      scanned_ranges: [],
      uncovered_ranges: [],
      older_history_unscanned: true,
    };
    const scope = partial.transactions?.scope;
    if (scope) {
      scope.scope_id = "transactions:2:early-match";
      scope.request_id = 2;
      scope.status = "partial";
      scope.verification = { status: "unverified", method: "partial coverage" };
      scope.discovery_coverage = transactions.discovery_coverage as never;
      scope.more_transactions_available = true;
    }

    await act(async () => renderTransactions(partial, requestTransactions, "view-a", null, {
      txListRows: [[HASH_A, 47_000_000, 1, "2026-07-18 10:00:00", 1, 1]],
      legRows: [],
    }));

    expect(requestTransactions).toHaveBeenCalledTimes(2);
    expect(requestTransactions).toHaveBeenLastCalledWith(expect.objectContaining({
      operation: "discover",
      cursor: "continue-after-first-match",
      pageSize: 25,
    }));
    expect(container.textContent).toContain("1 of 25 loaded");
    expect(container.textContent).not.toContain("Open receipt evidence or search address activity");
  });

  it("renders one actionable partial-empty state and no generic empty card", async () => {
    const discovery = serverFor([], 0, 25, SOURCE);
    const transactions = discovery.transactions as unknown as Record<string, unknown>;
    transactions.discovery_coverage = {
      complete: false,
      total_exact: null,
      total_lower_bound: 0,
      next_cursor: "opaque-coverage-cursor",
      scanned_ranges: [],
      uncovered_ranges: [{
        t0: "2018-10-08T00:00:00Z",
        t1: "2026-07-12T00:00:00Z",
        reason: "interactive budget reached",
      }],
      older_history_unscanned: true,
    };
    const scope = discovery.transactions?.scope;
    if (scope) {
      scope.status = "partial";
      scope.verification = { status: "unverified", method: "partial coverage" };
      scope.discovery_coverage = transactions.discovery_coverage as never;
    }

    await act(async () => renderTransactions(discovery, vi.fn(), "view-a", null, {
      legRows: [],
      txListRows: [],
    }));

    expect(container.querySelectorAll(".ge-tx-discovery-results")).toHaveLength(1);
    expect(container.textContent).toContain("No matches in the scanned portion yet");
    expect(container.textContent).toContain("Continue older history");
    expect(container.querySelector(".ge-tx-empty-discovery")).toBeNull();
    expect(container.textContent).not.toContain(
      "No matching transactions observed in the covered data",
    );
    expect(container.querySelector(".ge-tx-start-state")).toBeNull();
  });

  it("shows a failed address search without any neutral empty state", async () => {
    const failed = serverFor([], 0, 25, SOURCE);
    const transactions = failed.transactions as unknown as Record<string, unknown>;
    transactions.last_attempt = {
      request_id: 8,
      status: "failed",
      query_kind: "address_discovery",
      error: "address query timed out",
      retryable: true,
    };
    await act(async () => renderTransactions(failed, vi.fn(), "view-a", "address query timed out", {
      legRows: [],
      txListRows: [],
    }));

    expect(container.querySelector(".ge-load-error")?.textContent).toContain(
      "Address discovery failed",
    );
    expect(container.querySelector(".ge-tx-discovery-results")).toBeNull();
    expect(container.querySelector(".ge-tx-empty-discovery")).toBeNull();
    expect(container.querySelector(".ge-tx-start-state")).toBeNull();
  });

  it("hides an address failure immediately when switching to a clean hash query", async () => {
    const failed = serverFor([], 0, 25, SOURCE);
    const transactions = failed.transactions as unknown as Record<string, unknown>;
    transactions.last_attempt = {
      request_id: 9,
      status: "failed",
      query_kind: "address_discovery",
      error: "address query timed out",
      retryable: true,
    };
    await act(async () => renderTransactions(failed, vi.fn(), "view-a", "address query timed out", {
      legRows: [],
      txListRows: [],
    }));
    await act(async () => {
      container.querySelectorAll<HTMLButtonElement>(".ge-tx-query-kind button")[0]?.click();
    });

    expect(container.querySelector(".ge-load-error")).toBeNull();
    expect(container.textContent).not.toContain("FAILED RECEIPT INSPECTION");
    expect(container.querySelector(".ge-evidence-trigger")).toBeNull();
    expect(container.querySelector(".ge-tx-start-state")?.textContent).toContain(
      "Open receipt evidence",
    );
  });

  it("adopts an explicit-hash receipt even when the generic scope is stale discovery", async () => {
    await act(async () => renderTransactions(serverFor([], 0, 25, SOURCE), vi.fn()));

    const explicit = serverFor([HASH_A]);
    const transactions = explicit.transactions as unknown as Record<string, unknown>;
    const receiptScope = structuredClone(explicit.transactions?.scope ?? {});
    transactions.query = {
      kind: "hash",
      hashes: [HASH_A],
      address: null,
      counterparties: [],
      tokens: [],
      window: null,
    };
    transactions.result_hashes = [HASH_A];
    transactions.receipt_scope = receiptScope;
    if (explicit.transactions?.scope) {
      explicit.transactions.scope.query_kind = "address_discovery";
      explicit.transactions.scope.window = {
        t0: null,
        t1: null,
        source: "execution_tables_plus_rpc_head",
      };
    }
    const legRows = Array.from({ length: 21 }, (_, index) => {
      const row = txLeg(HASH_A, index);
      row[0] = `leg:${HASH_A}:${index}`;
      return row;
    });

    await act(async () => renderTransactions(explicit, vi.fn(), "view-a", null, {
      legRows,
    }));

    expect(container.querySelector(".ge-tx-table-region")).not.toBeNull();
    expect(container.querySelectorAll(".ge-tx-table tbody tr")).toHaveLength(21);
    expect(container.querySelector(".ge-tx-start-state")).toBeNull();
    expect(container.querySelector(`code[title='${HASH_A}']`)).not.toBeNull();
  });

  it("keeps applied receipt rows visible without relabelling an address failure", async () => {
    const applied = serverFor([HASH_A]);
    const transactions = applied.transactions as unknown as Record<string, unknown>;
    transactions.last_attempt = {
      request_id: 9,
      status: "failed",
      query_kind: "address_discovery",
      error: "Transaction discovery query timed out",
      retryable: true,
    };
    await act(async () => renderTransactions(applied));

    expect(container.querySelectorAll(".ge-tx-table tbody tr")).toHaveLength(1);
    expect(container.textContent).not.toContain("FAILED RECEIPT INSPECTION");
    expect(container.textContent).not.toContain("Address discovery failed");
    expect(container.textContent).toContain(HASH_A);
  });

  it("retries the exact pending transaction subject after a failure", async () => {
    const requestTransactions = vi.fn();
    const empty = serverFor([]);
    await act(async () => renderTransactions(empty, requestTransactions));
    const input = container.querySelector<HTMLInputElement>("input.ge-tx-input");
    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      setValue?.call(input, HASH_A);
      input?.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () =>
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent?.includes("Open"))
        ?.click(),
    );

    await act(async () =>
      renderTransactions(empty, requestTransactions, "view-a", "receipt timed out"),
    );
    await act(async () =>
      [...container.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "Retry")
        ?.click(),
    );
    expect(requestTransactions).toHaveBeenLastCalledWith({
      operation: "receipt",
      txHashes: [HASH_A],
      seed: "",
      // Every request now stamps the chain; asserting it explicitly locks the
      // Gnosis default rather than merely tolerating the new field.
      chain: "100",
    });
  });
});
