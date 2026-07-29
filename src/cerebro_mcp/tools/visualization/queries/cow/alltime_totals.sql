WITH @shared_ctes
SELECT t.chain_id AS chain_id,
       uniq(@trade_key) AS fill_count,uniq(t.tx_hash) AS settlement_transactions,
       uniq(t.owner) AS unique_traders,
       minOrNull(t.block_timestamp) AS first_trade_at,
       maxOrNull(t.block_timestamp) AS last_trade_at,
       minOrNull(t.block_timestamp) AS indexed_from,
       maxOrNull(t.block_timestamp) AS indexed_to,
       max(t.observed_at) AS source_observed_at
FROM cow_db.trades AS t
INNER JOIN cp ON cp.chain_id=t.chain_id
WHERE t.environment={env:String} AND t.chain_id IN (@ids) AND t.block_number<=cp.b
GROUP BY t.chain_id
ORDER BY t.chain_id
