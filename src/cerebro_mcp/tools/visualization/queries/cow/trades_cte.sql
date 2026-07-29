
tr AS (
  SELECT t.chain_id AS chain_id,uniq(@trade_key) AS a,uniq(tx_hash) AS b,
         minOrNull(block_timestamp) AS c,maxOrNull(block_timestamp) AS d,
         maxOrNull(observed_at) AS e
  FROM cow_db.trades AS t
  INNER JOIN cp ON cp.chain_id=t.chain_id
  WHERE t.environment={env:String} AND t.chain_id IN (@ids)
    AND t.block_number<=cp.b AND @arm_window
  GROUP BY t.chain_id
)
