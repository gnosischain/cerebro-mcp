
SELECT toStartOfDay(t.block_timestamp) AS bucket,@cid AS chain_id,
       uniq(@trade_key) AS fill_count,uniq(t.tx_hash) AS settlement_transactions,
       uniq(t.owner) AS owners,min(t.block_timestamp) AS indexed_from,
       max(t.block_timestamp) AS indexed_to,max(t.observed_at) AS source_observed_at
FROM cow_db.trades AS t
WHERE @arm_where
GROUP BY bucket
