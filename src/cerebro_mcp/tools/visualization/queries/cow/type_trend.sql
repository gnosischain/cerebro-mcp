WITH @oa_cte
SELECT toStartOfDay(creation_date) AS bucket,chain_id,order_class,
       count() AS order_count,countIf(status='fulfilled') AS fulfilled_count,
       min(creation_date) AS indexed_from,max(creation_date) AS indexed_to,
       max(obs_at) AS source_observed_at
FROM (@o_dedup)
GROUP BY bucket,chain_id,order_class
ORDER BY bucket,chain_id,order_class
