
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
  -- Canonical re-delegation (dbt api_governance_delegation_activity_monthly's
  -- 'repointed'): a set whose row_number over ALL of that (chain, delegator)'s
  -- events — sets AND clears — is > 1. The previous form,
  -- countIf(set) - uniqExactIf((chain_id, delegator), set), counted sets
  -- beyond the first SET, which undercounts a delegator whose FIRST event was
  -- a clear (all of their sets are re-points under the canonical rule). Same
  -- window spec as delegation_churn.sql so the two files cannot disagree.
  (SELECT countIf(action = 'SetDelegate' AND rn > 1)
     FROM (
       SELECT action,
              row_number() OVER (PARTITION BY chain_id, delegator
                                 ORDER BY block_number, log_index) AS rn
       FROM @src
     )) AS re_delegations,
  (SELECT countIf(action = 'ClearDelegate') / nullIf(countIf(action = 'SetDelegate'), 0)
     FROM @src) AS clear_rate
FROM active
ORDER BY active_delegators
