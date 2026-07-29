
SELECT min(observed_at) AS earliest_supported_at,
       max(observed_at) AS latest_observed_at,
       uniq(order_uid) AS captured_orders,
       min(creation_date) AS earliest_creation_seen,
       max(observed_at) AS source_observed_at
FROM cow_db.orders
WHERE environment={env:String} AND chain_id={chain_id:UInt64}
ORDER BY earliest_supported_at
