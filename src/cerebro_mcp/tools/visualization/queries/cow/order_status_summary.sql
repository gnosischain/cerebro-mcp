
SELECT o.status,count() AS order_count,uniqExact(o.owner) AS owners,
       min(o.creation_date) AS indexed_from,max(o.creation_date) AS indexed_to,
       max(o.observed_at) AS source_observed_at
FROM cow_db.orders AS o FINAL
WHERE @where
GROUP BY o.status
ORDER BY order_count DESC,o.status
