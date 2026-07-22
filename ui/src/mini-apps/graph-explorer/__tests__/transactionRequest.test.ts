import { describe, expect, it } from "vitest";

import { buildTransactionRequest } from "../transactionRequest";
import type { TransactionsState } from "../types";

const HASH_A = `0x${"aa".repeat(32)}`;
const HASH_B = `0x${"bb".repeat(32)}`;
const SEED = `0x${"11".repeat(20)}`;
const TARGET = `0x${"22".repeat(20)}`;
const TOKEN = `0x${"33".repeat(20)}`;

function applied(
  source: string,
  overrides: Partial<TransactionsState> = {},
): TransactionsState {
  return {
    tx_hashes: [HASH_A, HASH_B],
    seed: SEED,
    counterparties: [TARGET],
    tokens: [TOKEN],
    range_days: 30,
    max_txs: 25,
    min_usd: 5,
    t0: "2026-06-01 00:00:00",
    t1: "2026-07-01 00:00:00",
    scope: {
      window: {
        t0: "2026-06-01 00:00:00",
        t1: "2026-07-01 00:00:00",
        source,
      },
    },
    ...overrides,
  } as unknown as TransactionsState;
}

describe("buildTransactionRequest", () => {
  it("ignores legacy date ranges for complete indexed history plus RPC head", () => {
    const state = applied("range_days=30", {
      counterparties: [],
      tokens: [],
    });
    const request = buildTransactionRequest("view-1", state, {
      seed: SEED,
      counterparties: [],
      tokens: [],
      rangeDays: 1,
    });

    expect(request).toMatchObject({
      tx_hashes: [],
      seed_node_id: SEED,
      counterparty_ids: [],
      tokens: [],
      range_days: 0,
      t0: "",
      t1: "",
    });
  });

  it("keeps filtered address discovery all-history without an explicit UTC pair", () => {
    const request = buildTransactionRequest(
      "view-1",
      applied("range_days=30"),
      {
        operation: "discover",
        seed: SEED,
        tokens: [TOKEN],
        activityKinds: ["erc20"],
        t0: "",
        t1: "",
      },
    );

    expect(request).toMatchObject({
      operation: "discover",
      seed_node_id: SEED,
      tokens: [TOKEN],
      activity_kinds: ["erc20"],
      range_days: 0,
      t0: "",
      t1: "",
    });
  });

  it("retains applied discovery filters for pagination and count-only reloads", () => {
    const state = applied("execution_tables_plus_rpc_head");
    Object.assign(state as unknown as Record<string, unknown>, {
      query: {
        kind: "address",
        address: SEED,
        counterparties: [TARGET],
        tokens: [TOKEN],
        activity_kinds: ["erc20"],
        window: {
          t0: "2026-07-01T00:00:00.000Z",
          t1: "2026-07-19T12:30:00.000Z",
          source: "custom_utc_window",
        },
      },
    });

    const request = buildTransactionRequest("view-1", state, {
      operation: "discover",
      cursor: "opaque-page",
      pageSize: 25,
    });

    expect(request).toMatchObject({
      seed_node_id: SEED,
      counterparty_ids: [TARGET],
      tokens: [TOKEN],
      activity_kinds: ["erc20"],
      t0: "2026-07-01T00:00:00.000Z",
      t1: "2026-07-19T12:30:00.000Z",
      cursor: "opaque-page",
    });
  });

  it("preserves a newly supplied exact UTC pair", () => {
    const request = buildTransactionRequest(
      "view-1",
      applied("range_days=30", { counterparties: [], tokens: [] }),
      {
        operation: "discover",
        seed: SEED,
        t0: "2026-07-18 00:00:00",
        t1: "2026-07-19 00:00:00",
      },
    );

    expect(request).toMatchObject({
      range_days: 30,
      t0: "2026-07-18 00:00:00",
      t1: "2026-07-19 00:00:00",
    });
  });

  it("never promotes discovered hashes or a legacy Range into a new address reload", () => {
    const request = buildTransactionRequest(
      "view-1",
      applied("range_days=30"),
      { rangeDays: 90 },
    );

    expect(request).toMatchObject({
      tx_hashes: [],
      seed_node_id: SEED,
      counterparty_ids: [TARGET],
      tokens: [TOKEN],
      range_days: 0,
      max_txs: 25,
      t0: "",
      t1: "",
    });
  });

  it("preserves an exact Money window for count edits but retires it to all-history", () => {
    const exact = applied("money_trail_applied_window");
    expect(buildTransactionRequest("view-1", exact, { maxTxs: 50 })).toMatchObject({
      tx_hashes: [],
      seed_node_id: SEED,
      max_txs: 50,
      t0: "2026-06-01 00:00:00",
      t1: "2026-07-01 00:00:00",
    });
    expect(buildTransactionRequest("view-1", exact, { rangeDays: 90 })).toMatchObject({
      tx_hashes: [],
      seed_node_id: SEED,
      range_days: 0,
      t0: "",
      t1: "",
    });
  });

  it("implicitly reuses hashes only for a scope proven to be explicit", () => {
    const request = buildTransactionRequest(
      "view-1",
      applied("ignored_for_explicit_hash", { seed: "" }),
      { maxTxs: 50 },
    );
    expect(request).toMatchObject({
      tx_hashes: [HASH_A, HASH_B],
      seed_node_id: "",
      max_txs: 50,
      t0: "",
      t1: "",
    });
  });

  it("reuses explicit query hashes, never unrelated result hashes", () => {
    const state = applied("ignored_for_explicit_hash", { seed: "" });
    Object.assign(state as unknown as Record<string, unknown>, {
      query_kind: "explicit_hash",
      query_hashes: [HASH_B],
      result_hashes: [HASH_A, HASH_B],
    });
    const request = buildTransactionRequest("view-1", state, {});
    expect(request).toMatchObject({
      tx_hashes: [HASH_B],
      seed_node_id: "",
    });
  });

  it("keeps follow-expansion on the legacy cursor-to-head contract", () => {
    const request = buildTransactionRequest(
      "view-1",
      applied("ignored_for_explicit_hash", { seed: "" }),
      {
        operation: "legacy",
        expandNodeId: TARGET,
        afterBlock: 123,
        afterIndex: 7,
        merge: true,
      },
    );
    expect(request).toMatchObject({
      operation: "legacy",
      tx_hashes: [],
      expand_node_id: TARGET,
      after_block: 123,
      after_index: 7,
      merge: true,
      range_days: 0,
    });
  });
});
