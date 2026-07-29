
WITH fills AS (
  SELECT chain_id,tx_hash, uniq(tuple(log_index,order_uid)) AS fill_count
  FROM cow_db.trades
  WHERE @feed_pred
    AND block_timestamp >= @live_window
  GROUP BY chain_id,tx_hash
)
SELECT u.block_ts AS block_timestamp,u.chain_id AS chain_id,
       u.tx_hash,u.block_num AS block_number,
       u.settlement_executor,
       coalesce(fills.fill_count,0) AS fill_count,
       u.obs_at AS source_observed_at
FROM (
  -- block_num, NOT block_number: aliasing an aggregate to a column name that
  -- also appears in a same-level WHERE makes ClickHouse bind the WHERE
  -- identifier to the aggregate → ILLEGAL_AGGREGATION (code 184). A distinct
  -- alias keeps this safe even if a block_number predicate is added later.
  SELECT chain_id,tx_hash,log_index,
         argMax(block_timestamp,observed_at) AS block_ts,
         argMax(block_number,observed_at) AS block_num,
         argMax(solver,observed_at) AS settlement_executor,
         max(observed_at) AS obs_at
  FROM cow_db.settlements
  WHERE @feed_pred
    AND block_timestamp >= @live_window
  GROUP BY chain_id,tx_hash,log_index
  ORDER BY block_ts DESC,log_index DESC
  LIMIT 30
) AS u
LEFT JOIN fills ON fills.chain_id=u.chain_id AND fills.tx_hash=u.tx_hash
ORDER BY u.block_ts DESC,u.log_index DESC
LIMIT 30
