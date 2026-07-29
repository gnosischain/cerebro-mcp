
SELECT voter_key, voter_display AS voter,
       vote_count, total_vp, avg_vp, first_vote_at, last_vote_at
FROM (
  SELECT lower(voter) AS voter_key, any(voter) AS voter_display,
         count() AS vote_count, sum(vp) AS total_vp, avg(vp) AS avg_vp,
         min(created_at) AS first_vote_at, max(created_at) AS last_vote_at
  FROM governance_db.snapshot_votes FINAL
  WHERE @votes_time
  GROUP BY voter_key
)
ORDER BY @sort_fragment
