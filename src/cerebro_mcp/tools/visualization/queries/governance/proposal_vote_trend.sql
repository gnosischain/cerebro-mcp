
SELECT bucket, votes, round(vp) AS vp,
       round(sum(votes) OVER (ORDER BY bucket)) AS cumulative_votes,
       round(sum(vp) OVER (ORDER BY bucket)) AS cumulative_vp,
       'hour' AS bucket_unit
FROM (
  SELECT toStartOfHour(created_at) AS bucket, count() AS votes, sum(vp) AS vp
  FROM governance_db.snapshot_votes FINAL
  WHERE proposal_id = {proposal_id:String}
  GROUP BY bucket
)
ORDER BY bucket
