
fs AS (
  SELECT chain_id, owner, min(block_timestamp) AS first_seen
  FROM cow_db.trades
  WHERE environment={env:String} AND chain_id IN (@ids)
    AND block_timestamp IS NOT NULL
    AND block_timestamp >= (SELECT max(a) FROM ta) - toIntervalDay(@correlation_window_days)
  GROUP BY chain_id, owner
)
