
SELECT bucket, count() AS fills,
       avg(surplus_bps) AS avg_surplus_bps,
       quantile(0.5)(surplus_bps) AS median_surplus_bps,
       avgIf(latency_seconds, latency_seconds IS NOT NULL) AS avg_latency_seconds,
       quantileIf(0.5)(latency_seconds, latency_seconds IS NOT NULL) AS median_latency_seconds,
       min(block_timestamp) AS indexed_from, max(block_timestamp) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM (@quality_source)
GROUP BY bucket
ORDER BY bucket
