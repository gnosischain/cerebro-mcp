
SELECT multiIf(latency_seconds IS NULL,'unknown',
               latency_seconds<10,'<10s',
               latency_seconds<60,'10-60s',
               latency_seconds<300,'1-5m',
               latency_seconds<3600,'5-60m','>1h') AS latency_bucket,
       count() AS fills,
       min(block_timestamp) AS indexed_from, max(block_timestamp) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM (@quality_source)
GROUP BY latency_bucket
ORDER BY latency_bucket
