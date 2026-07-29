
WITH active AS (
  SELECT chain_id, delegator,
         argMax(action, (block_number, log_index)) AS last_action,
         argMax(delegate, (block_number, log_index)) AS current_delegate
  FROM @src
  GROUP BY chain_id, delegator
)
SELECT
  uniqExactIf(delegator, last_action = 'SetDelegate') AS active_delegators,
  uniqExactIf(current_delegate, last_action = 'SetDelegate') AS active_delegates,
  (SELECT count() FROM @src) AS total_events,
  (SELECT countIf(action = 'SetDelegate') FROM @src) AS set_events,
  (SELECT countIf(action = 'ClearDelegate') FROM @src) AS clear_events,
  (SELECT countIf(action = 'SetDelegate') - uniqExactIf((chain_id, delegator), action = 'SetDelegate')
     FROM @src) AS re_delegations,
  (SELECT countIf(action = 'ClearDelegate') / nullIf(countIf(action = 'SetDelegate'), 0)
     FROM @src) AS clear_rate
FROM active
ORDER BY active_delegators
