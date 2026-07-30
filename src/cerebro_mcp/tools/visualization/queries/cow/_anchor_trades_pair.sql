-- Latest indexed trade timestamp FOR ONE PAIR, as a scalar anchor.
--
-- Deliberately not _anchor_trades.sql with an extra predicate: this one is
-- pair-scoped in both directions (sell/buy either way round) and is spread over
-- several lines, so the two render different bytes and the callers are not
-- interchangeable. Same base-table rationale as _anchor_trades.sql — max() over
-- raw RMT versions equals max() over the deduped view, without paying FINAL.
--
-- The chain is bound, not composed: this anchor is only ever used single-chain.
SELECT max(block_timestamp) FROM cow_db.trades
WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  AND ((sell_token={base:String} AND buy_token={quote:String})
       OR (sell_token={quote:String} AND buy_token={base:String}))
  AND block_timestamp IS NOT NULL
