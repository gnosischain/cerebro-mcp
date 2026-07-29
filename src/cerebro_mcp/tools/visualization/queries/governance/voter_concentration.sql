
WITH per_voter AS (
  SELECT lower(voter) AS voter_key, count() AS vote_count, sum(vp) AS total_vp
  FROM governance_db.snapshot_votes FINAL
  WHERE @votes_time
  GROUP BY voter_key
),
by_vp AS (
  SELECT groupArray(total_vp) AS sorted_values, sum(total_vp) AS total_value
  FROM (SELECT total_vp FROM per_voter ORDER BY total_vp DESC)
),
by_votes AS (
  SELECT groupArray(toFloat64(vote_count)) AS sorted_values,
         sum(toFloat64(vote_count)) AS total_value
  FROM (SELECT vote_count FROM per_voter ORDER BY vote_count DESC)
)
SELECT metric, tier, tier_value, total_value,
       tier_value / nullIf(total_value, 0) AS share
FROM (
  SELECT 'vp' AS metric, tier,
         arraySum(arraySlice(sorted_values, 1, tier)) AS tier_value, total_value
  FROM by_vp
  ARRAY JOIN [toUInt32(10), toUInt32(20), toUInt32(50)] AS tier
  UNION ALL
  SELECT 'votes', tier,
         arraySum(arraySlice(sorted_values, 1, tier)), total_value
  FROM by_votes
  ARRAY JOIN [toUInt32(10), toUInt32(20), toUInt32(50)] AS tier
)
ORDER BY metric, tier
