
exec AS (
  -- Base settlements (NOT the settlements_canonical view, whose FINAL +
  -- chain_blocks materialization OOMed the box). The block_timestamp bound
  -- keeps the GROUP BY tx_hash hash to ~30d of txs (small); the scan streams.
  -- The resulting tx_hash set is this solver's settlement txs — and tx_hash IS
  -- in the trades sort key (environment, chain_id, tx_hash, log_index), so it
  -- PRUNES the trades_canonical scan in `flows` (block_number would NOT — it is
  -- not in the sort key).
  SELECT tx_hash, argMax(solver,tuple(block_timestamp,log_index)) AS s
  FROM cow_db.settlements
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND block_timestamp >= (
      SELECT max(block_timestamp) FROM cow_db.settlements
      WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    ) - toIntervalDay(30)
  GROUP BY tx_hash
  HAVING s={id:String}
),
fills_d AS (
  -- Deduped base trades for this solver's settlement txs. tx_hash IN (…) PRUNES
  -- (tx_hash is in the sort key) to ~125k rows; argMax over the RMT version key
  -- dedups WITHOUT touching trades_canonical (whose internal chain_blocks-FINAL
  -- reorg-join would scan ~2.2M chain_blocks regardless). Settlements 30d back
  -- are committed/final, so the reorg-safe view buys nothing here.
  SELECT tx_hash, log_index, order_uid,
         argMax(block_timestamp,observed_at) AS block_timestamp,
         argMax(sell_token,observed_at) AS sell_token,
         argMax(buy_token,observed_at) AS buy_token,
         argMax(sell_amount,observed_at) AS sell_amount,
         argMax(buy_amount,observed_at) AS buy_amount
  FROM cow_db.trades
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND tx_hash IN (SELECT tx_hash FROM exec)
  GROUP BY tx_hash, log_index, order_uid
),
flows AS (
  SELECT u.tx_hash AS tx_hash, any(u.block_timestamp) AS block_timestamp,
         u.token AS token, sum(u.amt) AS net_atoms
  FROM (
    SELECT tx_hash, block_timestamp, sell_token AS token, toInt256(sell_amount) AS amt FROM fills_d
    UNION ALL
    SELECT tx_hash, block_timestamp, buy_token, -toInt256(buy_amount) FROM fills_d
  ) AS u
  GROUP BY u.tx_hash, u.token
),
am AS (
  SELECT tx_hash, argMax(auction_id,observed_at) AS auction_id
  FROM cow_db.competition_transactions FINAL
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  GROUP BY tx_hash
),
pr AS (
  SELECT auction_id, token, argMax(price,observed_at) AS price
  FROM cow_db.auction_prices FINAL
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  GROUP BY auction_id, token
)
