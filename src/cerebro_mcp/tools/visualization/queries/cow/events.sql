
SELECT argMax(event_type,observed_at) AS event_type,chain_id,
       argMax(order_uid,observed_at) AS order_uid,
       argMax(owner,observed_at) AS owner,
       argMax(block_number,observed_at) AS block_number,
       argMax(transaction_hash,observed_at) AS transaction_hash,
       argMax(event_timestamp,observed_at) AS event_timestamp,
       max(observed_at) AS source_observed_at
FROM cow_db.order_events
WHERE @feed_pred
  AND observed_at >= now() - INTERVAL 1 HOUR
GROUP BY chain_id,event_id
ORDER BY source_observed_at DESC,event_id DESC
LIMIT 50
