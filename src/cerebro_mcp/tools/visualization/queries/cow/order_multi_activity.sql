WITH @oa_cte
SELECT toStartOfDay(creation_date) AS bucket,count() AS order_count,
       countIf(status='open') AS currently_open,
       min(creation_date) AS indexed_from,max(creation_date) AS indexed_to,
       max(obs_at) AS source_observed_at
FROM (@o_dedup)
WHERE @outer
GROUP BY bucket
ORDER BY bucket
