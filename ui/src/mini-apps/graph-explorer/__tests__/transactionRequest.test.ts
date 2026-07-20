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
      t0: "2026-07-18 00:00:00",
      t1: "2026-07-19 00:00:00",
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

  it("never promotes address-discovered hashes into an explicit Range reload", () => {
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
      range_days: 90,
      max_txs: 25,
      t0: "",
      t1: "",
    });
  });

  it("preserves an exact Money window for Txs edits but retires it for Range edits", () => {
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
      range_days: 90,
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

  it("forces follow-expansion back onto address discovery", () => {
    const request = buildTransactionRequest(
      "view-1",
      applied("ignored_for_explicit_hash", { seed: "" }),
      { expandNodeId: TARGET, afterBlock: 123, afterIndex: 7, merge: true },
    );
    expect(request).toMatchObject({
      tx_hashes: [],
      expand_node_id: TARGET,
      after_block: 123,
      after_index: 7,
      merge: true,
    });
  });
});
