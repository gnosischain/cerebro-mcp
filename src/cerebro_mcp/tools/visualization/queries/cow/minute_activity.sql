
SELECT toStartOfMinute(block_timestamp) AS bucket,chain_id,
       uniq(@trade_key) AS fills,uniq(tx_hash) AS settlements,
       min(block_timestamp) AS indexed_from,max(block_timestamp) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM cow_db.trades
WHERE @feed_pred
  AND block_timestamp >= @live_window
GROUP BY bucket,chain_id
ORDER BY bucket,chain_id
