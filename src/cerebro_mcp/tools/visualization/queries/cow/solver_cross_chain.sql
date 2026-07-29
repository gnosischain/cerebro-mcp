
WITH @blocks_cte
SELECT s.solver AS competition_solver, s.chain_id AS chain_id,
       count() AS solutions, uniqExact(s.auction_id) AS competitions,
       countIf(s.is_winner) AS wins,
       countIf(s.is_winner)/nullIf(uniqExact(s.auction_id),0) AS win_rate,
       min(s.ranking) AS best_ranking,
       min(b.block_timestamp) AS indexed_from,max(b.block_timestamp) AS indexed_to,
       max(s.observed_at) AS source_observed_at
@common
GROUP BY competition_solver, chain_id
ORDER BY competition_solver, chain_id
LIMIT 2000
