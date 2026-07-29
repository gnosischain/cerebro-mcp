
WITH active AS (
  SELECT chain_id, delegator,
         argMax(action, (block_number, log_index)) AS last_action,
         argMax(delegate, (block_number, log_index)) AS current_delegate
  FROM @src
  GROUP BY chain_id, delegator
),
delegates AS (
  SELECT current_delegate AS delegate, uniqExact(delegator) AS delegator_count
  FROM active
  WHERE last_action = 'SetDelegate'
  GROUP BY current_delegate
),
slots AS (
  SELECT proposal_id,
         arrayFilter(i -> position(names[i], '@delegation_match') > 0
                          AND networks[i] = '1', arrayEnumerate(names)) AS slots_mainnet,
         arrayFilter(i -> position(names[i], '@delegation_match') > 0
                          AND networks[i] = '100', arrayEnumerate(names)) AS slots_gnosis
  FROM (
    SELECT id AS proposal_id,
           arrayMap(x -> JSONExtractString(x, 'name'),
                    JSONExtractArrayRaw(raw_json, 'strategies')) AS names,
           arrayMap(x -> JSONExtractString(x, 'network'),
                    JSONExtractArrayRaw(raw_json, 'strategies')) AS networks
    FROM @gov_db.snapshot_proposals FINAL
    WHERE space_id = '@space'
  )
),
latest_vote AS (
  SELECT lower(voter) AS voter_key,
         argMax((proposal_id,
                 JSONExtract(raw_json, 'vp_by_strategy', 'Array(Float64)')),
                created_at) AS pick,
         max(created_at) AS last_vote_at
  FROM @gov_db.snapshot_votes FINAL
  WHERE vp_state = 'final' AND space_id = '@space'
  GROUP BY voter_key
)
SELECT d.delegate AS delegate,
       d.delegator_count AS delegator_count,
       -- A delegate who never voted misses the LEFT JOIN, and DateTime has no
       -- NULL of its own — it defaults to the epoch. Printing "1970-01-01" as
       -- a vote date is the same sin as printing 0 for an unmeasured balance.
       nullIf(lv.last_vote_at, toDateTime(0)) AS last_vote_at,
       if(empty(s.slots_gnosis), NULL,
          arraySum(arrayMap(i -> lv.pick.2[i], s.slots_gnosis))) AS delegated_vp_gnosischain,
       if(empty(s.slots_mainnet), NULL,
          arraySum(arrayMap(i -> lv.pick.2[i], s.slots_mainnet))) AS delegated_vp_mainnet,
       if(empty(s.slots_mainnet) AND empty(s.slots_gnosis), NULL,
          arraySum(arrayMap(i -> lv.pick.2[i],
                            arrayConcat(s.slots_mainnet, s.slots_gnosis)))) AS delegated_vp_total
FROM delegates AS d
LEFT JOIN latest_vote AS lv ON lv.voter_key = lower(d.delegate)
LEFT JOIN slots AS s ON s.proposal_id = lv.pick.1
ORDER BY delegated_vp_total DESC NULLS LAST, delegate
LIMIT @cap
