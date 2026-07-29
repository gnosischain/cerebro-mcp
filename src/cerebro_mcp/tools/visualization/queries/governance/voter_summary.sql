
WITH per_voter AS (
  SELECT lower(voter) AS voter_key, count() AS pv_votes, sum(vp) AS pv_vp
  FROM governance_db.snapshot_votes FINAL
  WHERE @votes_time
  GROUP BY voter_key
)
SELECT count() AS voter_count,
       sum(pv_vp) AS total_vp,
       sum(pv_votes) AS vote_count,
       avg(pv_votes) AS avg_participation,
       quantileExact(0.5)(pv_votes) AS median_participation,
       countIf(pv_votes > 1) / nullIf(count(), 0) AS repeat_rate,
       (SELECT count() FROM governance_db.snapshot_follows FINAL) AS follower_count
FROM per_voter
ORDER BY voter_count
