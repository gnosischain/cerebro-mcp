
WITH per_delegate AS (
  SELECT current_delegate AS delegate, uniqExact(delegator) AS delegator_count
  FROM (
    SELECT chain_id, delegator,
           argMax(delegate, (block_number, log_index)) AS current_delegate,
           argMax(action, (block_number, log_index)) AS last_action
    FROM @src
    GROUP BY chain_id, delegator
  )
  WHERE last_action = 'SetDelegate'
  GROUP BY current_delegate
),
sorted AS (
  SELECT groupArray(toFloat64(delegator_count)) AS values,
         sum(toFloat64(delegator_count)) AS total_value
  FROM (SELECT delegator_count FROM per_delegate ORDER BY delegator_count DESC)
)
SELECT tier,
       arraySum(arraySlice(values, 1, tier)) AS tier_value,
       total_value,
       arraySum(arraySlice(values, 1, tier)) / nullIf(total_value, 0) AS share
FROM sorted
ARRAY JOIN [toUInt32(5), toUInt32(10), toUInt32(20)] AS tier
ORDER BY tier
