WITH @oa_cte
SELECT chain_id,order_class,count() AS order_count,uniqExact(owner) AS owners,
       countIf(status='fulfilled') AS fulfilled,countIf(status='expired') AS expired,
       countIf(status='cancelled') AS cancelled,countIf(status='open') AS open_now,
       countIf(status='fulfilled')/nullIf(count(),0) AS fulfilled_share,
       countIf(partially_fillable) AS partially_fillable_count,
       min(creation_date) AS indexed_from,max(creation_date) AS indexed_to,
       max(obs_at) AS source_observed_at
FROM (@o_dedup)
GROUP BY chain_id,order_class
ORDER BY chain_id,order_count DESC
