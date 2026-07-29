WITH @shared_ctes
SELECT @share_bucket AS bucket,t.chain_id AS chain_id,
       uniq(@trade_key) AS fill_count,uniq(t.tx_hash) AS settlement_transactions,
       min(t.block_timestamp) AS indexed_from,max(t.block_timestamp) AS indexed_to,
       max(t.observed_at) AS source_observed_at
FROM cow_db.trades AS t
INNER JOIN cp ON cp.chain_id=t.chain_id
WHERE t.environment={env:String} AND t.chain_id IN (@ids)
  AND t.block_number<=cp.b AND t.block_timestamp IS NOT NULL
  AND @arm_window
GROUP BY bucket,t.chain_id
ORDER BY bucket,t.chain_id
