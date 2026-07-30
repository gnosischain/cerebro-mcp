-- Per-UNION-arm trade anchor for ONE chain, parenthesised for inline use.
--
-- @chain_id is substituted, NOT bound. It cannot be a ClickHouse parameter: one
-- statement carries a separate arm per chain, so a single {chain_id:UInt64}
-- binding would have to hold every chain's id at once. The value comes from the
-- static chain registry, never from user input.
--
-- Base table, not the canonical view — see _anchor_trades.sql for why.
(SELECT max(block_timestamp) FROM cow_db.trades WHERE environment={env:String} AND chain_id=@chain_id)
