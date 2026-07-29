WITH @oa_cte
SELECT chain_id,order_kind,signing_scheme,partially_fillable,
       count() AS order_count,uniqExact(owner) AS owners,
       countIf(status='fulfilled')/nullIf(count(),0) AS fulfilled_share,
       min(creation_date) AS indexed_from,max(creation_date) AS indexed_to,
       max(obs_at) AS source_observed_at
FROM (@o_dedup)
GROUP BY chain_id,order_kind,signing_scheme,partially_fillable
ORDER BY chain_id,order_count DESC
