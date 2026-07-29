
WITH @blocks_cte,
exec AS (
  SELECT solver, uniq(tx_hash) AS executed_settlements
  FROM cow_db.settlements
  WHERE environment={env:String} AND chain_id IN (@ids) AND @settlement_time
  GROUP BY solver
)
SELECT s.solver AS competition_solver,count() AS solutions,
       uniqExact(s.auction_id) AS competitions,countIf(s.is_winner) AS wins,
       countIf(s.is_winner)/nullIf(uniqExact(s.auction_id),0) AS win_rate,
       uniqExact(s.chain_id) AS chains_active,
       any(exec.executed_settlements) AS executed_settlements,
       avg(toFloat64(s.ranking)) AS average_ranking,min(s.ranking) AS best_ranking,
       min(b.block_timestamp) AS indexed_from,max(b.block_timestamp) AS indexed_to,
       max(s.observed_at) AS source_observed_at
@common_joins
LEFT JOIN exec ON exec.solver=s.solver
@common_where
GROUP BY competition_solver
ORDER BY wins DESC,competitions DESC,competition_solver
LIMIT 200
