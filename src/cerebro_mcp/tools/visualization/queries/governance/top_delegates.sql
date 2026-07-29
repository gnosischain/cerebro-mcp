
SELECT current_delegate AS delegate,
       uniqExact(delegator) AS delegator_count,
       min(set_at) AS first_delegation_at,
       max(set_at) AS last_delegation_at
FROM (
  SELECT chain_id, delegator,
         argMax(delegate, (block_number, log_index)) AS current_delegate,
         argMax(action, (block_number, log_index)) AS last_action,
         argMax(block_timestamp, (block_number, log_index)) AS set_at
  FROM @src
  GROUP BY chain_id, delegator
)
WHERE last_action = 'SetDelegate'
GROUP BY current_delegate
ORDER BY @sort_fragment
