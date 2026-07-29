
WITH sorted AS (
  SELECT groupArray(total_vp) AS vp_values, sum(total_vp) AS all_vp,
         count() AS voter_count
  FROM (
    SELECT lower(voter) AS voter_key, sum(vp) AS total_vp
    FROM governance_db.snapshot_votes FINAL
    WHERE @votes_time
    GROUP BY voter_key
    ORDER BY total_vp DESC
  )
)
SELECT tier,
       arraySum(arraySlice(vp_values, 1, tier)) AS tier_vp,
       all_vp,
       arraySum(arraySlice(vp_values, 1, tier)) / nullIf(all_vp, 0) AS vp_share,
       voter_count
FROM sorted
ARRAY JOIN [toUInt32(10), toUInt32(20), toUInt32(50)] AS tier
ORDER BY tier
