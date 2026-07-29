
SELECT @bucket_sql AS bucket,
       uniqExact(lower(voter)) AS unique_voters,
       count() AS vote_count,
       sum(vp) AS total_vp,
       '@unit' AS bucket_unit
FROM governance_db.snapshot_votes FINAL
WHERE @votes_time
GROUP BY bucket
ORDER BY bucket
