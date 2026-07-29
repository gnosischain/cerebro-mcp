
SELECT toStartOfDay(o.creation_date) AS bucket,count() AS order_count,
       countIf(o.status='open') AS currently_open,
       min(o.creation_date) AS indexed_from,max(o.creation_date) AS indexed_to,
       max(o.observed_at) AS source_observed_at
FROM cow_db.orders AS o FINAL
WHERE @where
GROUP BY bucket
ORDER BY bucket
