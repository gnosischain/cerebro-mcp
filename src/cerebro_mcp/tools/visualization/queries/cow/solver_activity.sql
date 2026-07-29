
WITH @blocks_cte
SELECT toStartOfDay(b.block_timestamp) AS bucket,s.solver AS competition_solver,
       uniqExact(s.auction_id) AS competitions,countIf(s.is_winner) AS wins,
       min(b.block_timestamp) AS indexed_from,max(b.block_timestamp) AS indexed_to,
       max(s.observed_at) AS source_observed_at
@common
GROUP BY bucket,competition_solver
ORDER BY bucket,competition_solver
