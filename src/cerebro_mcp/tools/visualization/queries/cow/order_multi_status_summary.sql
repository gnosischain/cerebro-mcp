WITH @oa_cte
SELECT status,count() AS order_count,uniqExact(owner) AS owners,
       min(creation_date) AS indexed_from,max(creation_date) AS indexed_to,
       max(obs_at) AS source_observed_at
FROM (@o_dedup)
WHERE @outer
GROUP BY status
ORDER BY order_count DESC,status
