
WITH st AS (
  SELECT chain_id,solver,
         minOrNull(block_timestamp) AS first_settlement_at,
         maxOrNull(block_timestamp) AS last_settlement_at,
         uniq(tx_hash) AS settlements_all_time,
         max(observed_at) AS obs_st
  FROM cow_db.settlements
  WHERE environment={env:String} AND chain_id IN (@ids)
  GROUP BY chain_id,solver
), cs AS (
  SELECT chain_id,solver,uniqExact(auction_id) AS competitions_all,
         countIf(is_winner) AS wins_all,
         max(observed_at) AS obs_cs
  FROM cow_db.competition_solutions FINAL
  WHERE environment={env:String} AND chain_id IN (@ids)
  GROUP BY chain_id,solver
), sa AS (
  SELECT chain_id,maxOrNull(block_timestamp) AS chain_anchor_at
  FROM cow_db.settlements
  WHERE environment={env:String} AND chain_id IN (@ids)
  GROUP BY chain_id
), dkeys AS (
  SELECT chain_id,solver FROM st
  UNION DISTINCT
  SELECT chain_id,solver FROM cs
)
SELECT k.chain_id AS chain_id,k.solver AS solver,
       st.first_settlement_at AS first_settlement_at,
       st.last_settlement_at AS last_settlement_at,
       coalesce(st.settlements_all_time,0) AS settlements_all_time,
       coalesce(cs.competitions_all,0) AS competitions_all,
       coalesce(cs.wins_all,0) AS wins_all,
       sa.chain_anchor_at AS chain_anchor_at,
       st.first_settlement_at AS indexed_from,
       st.last_settlement_at AS indexed_to,
       greatest(coalesce(st.obs_st,toDateTime64(0,3,'UTC')),
                coalesce(cs.obs_cs,toDateTime64(0,3,'UTC'))) AS source_observed_at
FROM dkeys AS k
LEFT JOIN st ON st.chain_id=k.chain_id AND st.solver=k.solver
LEFT JOIN cs ON cs.chain_id=k.chain_id AND cs.solver=k.solver
LEFT JOIN sa ON sa.chain_id=k.chain_id
ORDER BY settlements_all_time DESC,k.chain_id,k.solver
LIMIT 3000
