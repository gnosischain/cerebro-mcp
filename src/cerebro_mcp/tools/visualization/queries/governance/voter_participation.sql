
SELECT toStartOfMonth(created_at) AS bucket, count() AS vote_count,
       sum(vp) AS total_vp, 'month' AS bucket_unit
FROM governance_db.snapshot_votes FINAL
WHERE lower(voter) = {voter:String}
GROUP BY bucket
ORDER BY bucket
