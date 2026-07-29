SELECT chain_id,'order' AS entity_type,'order' AS role,count() AS evidence_count
FROM cow_db.orders FINAL WHERE @where AND order_uid={q:String}
GROUP BY chain_id ORDER BY chain_id
