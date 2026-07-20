import type { TxSettings } from "./modes/TransactionsView";
import type { TransactionsState } from "./types";

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}

/**
 * Build one complete Transaction Detail loader snapshot from local intent and
 * the last applied server scope.
 *
 * `transactions.tx_hashes` is ambiguous by itself: address discovery stores
 * the hashes it found there too. Only an `ignored_for_explicit_hash` scope may
 * implicitly reuse them as an explicit-hash subject. All other scopes retain
 * their discovery seed and filters.
 */
export function buildTransactionRequest(
  viewId: string,
  txState: TransactionsState | undefined,
  settings: Partial<TxSettings>,
): Record<string, unknown> | null {
  if (!viewId) return null;
  const applied = txState ?? {};
  const appliedRecord = applied as unknown as Record<string, unknown>;
  const scope = objectValue(applied.scope);
  const window = objectValue(scope.window);
  const windowSource = String(window.source ?? scope.window_source ?? "");
  const appliedExplicit =
    String(scope.query_kind ?? appliedRecord.query_kind ?? "") === "explicit_hash" ||
    windowSource === "ignored_for_explicit_hash";
  const expandNodeId = settings.expandNodeId ?? "";

  let hashes =
    settings.txHashes !== undefined
      ? settings.txHashes
      : appliedExplicit
        ? Array.isArray(appliedRecord.query_hashes)
          ? appliedRecord.query_hashes.map(String)
          : applied.tx_hashes ?? []
        : [];
  // Follow is discovery by address and cursor even when it starts from an
  // explicitly opened receipt.
  if (expandNodeId) hashes = [];

  const seed = settings.seed ?? applied.seed ?? "";
  if (!hashes.length && !seed && !expandNodeId) return null;

  const counterparties = settings.counterparties ?? applied.counterparties ?? [];
  const tokens = settings.tokens ?? applied.tokens ?? [];
  const allHistoryAddress = Boolean(
    !hashes.length && seed && (expandNodeId || (!counterparties.length && !tokens.length)),
  );

  const changingRelativeRange = settings.rangeDays !== undefined;
  const appliedExactWindow =
    windowSource === "money_trail_applied_window" ||
    windowSource === "custom_utc_window";
  const retainedT0 = String(applied.t0 ?? window.t0 ?? "");
  const retainedT1 = String(applied.t1 ?? window.t1 ?? "");
  const t0 = allHistoryAddress
    ? ""
    :
    settings.t0 !== undefined
      ? settings.t0
      : appliedExactWindow && !changingRelativeRange
        ? retainedT0
        : "";
  const t1 = allHistoryAddress
    ? ""
    :
    settings.t1 !== undefined
      ? settings.t1
      : appliedExactWindow && !changingRelativeRange
        ? retainedT1
        : "";

  return {
    view_id: viewId,
    tx_hashes: hashes,
    seed_node_id: hashes.length ? "" : seed,
    counterparty_ids: counterparties,
    tokens,
    t0,
    t1,
    // Plain address activity is complete execution history plus an RPC head;
    // follow is an RPC cursor-to-head scan.
    // A legacy txrange value may still be readable from an old URL, but it is
    // not allowed to narrow the discovery predicate.
    range_days: allHistoryAddress ? 0 : settings.rangeDays ?? applied.range_days ?? 30,
    max_txs: settings.maxTxs ?? applied.max_txs ?? 25,
    min_usd: settings.minUsd ?? applied.min_usd ?? 0,
    expand_node_id: expandNodeId,
    after_block: settings.afterBlock ?? 0,
    after_index: settings.afterIndex ?? -1,
    merge: settings.merge ?? false,
  };
}
