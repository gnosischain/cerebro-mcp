
WITH ord AS (
 SELECT app_data_hash FROM cow_db.orders FINAL
 WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND order_uid={id:String}
 LIMIT 1
)
SELECT a.app_data_hash,a.full_app_data,a.source,a.observed_at AS source_observed_at
FROM cow_db.app_data AS a FINAL
INNER JOIN ord ON a.app_data_hash=ord.app_data_hash
WHERE a.environment={env:String} AND a.chain_id={chain_id:UInt64}
ORDER BY a.observed_at DESC
