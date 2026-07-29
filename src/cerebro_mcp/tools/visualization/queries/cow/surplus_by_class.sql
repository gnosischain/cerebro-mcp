
SELECT klass AS order_class,
       multiIf(surplus_bps IS NULL,'unknown',
               surplus_bps< -50,'< -50 bps',
               surplus_bps<0,'-50-0 bps',
               surplus_bps<10,'0-10 bps',
               surplus_bps<50,'10-50 bps',
               surplus_bps<200,'50-200 bps','> 200 bps') AS surplus_bucket,
       count() AS fills,
       avgOrNull(surplus_bps) AS avg_surplus_bps,
       quantile(0.5)(surplus_bps) AS median_surplus_bps,
       min(block_timestamp) AS indexed_from, max(block_timestamp) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM (@class_source)
GROUP BY order_class, surplus_bucket
ORDER BY order_class, surplus_bucket
