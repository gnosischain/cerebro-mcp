-- Latest indexed trade timestamp, as a scalar anchor for a relative window.
--
-- Anchored on the BASE `trades` table, NOT `trades_canonical`: max() over the
-- duplicate ReplacingMergeTree versions is identical to max() over the deduped
-- view, and skipping the canonical view's FINAL + chain_blocks join keeps this
-- scalar subquery cheap. @scope comes from _scope_predicate() in Python, which
-- emits either `chain_id={chain_id:UInt64}` (single chain) or `chain_id IN (…)`
-- (all networks) — a predicate EXPRESSION, which is legitimate Python
-- composition; the STATEMENT lives here.
SELECT max(block_timestamp) FROM cow_db.trades WHERE @scope AND block_timestamp IS NOT NULL
