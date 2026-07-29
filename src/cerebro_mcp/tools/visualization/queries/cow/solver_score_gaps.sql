
WITH @blocks_cte
SELECT s.chain_id AS chain_id,s.solver AS competition_solver,
       count() AS wins_scored,
       countIf((@gap_expr) IS NULL) AS parse_failures,
       avgOrNull(@gap_expr) AS avg_score_gap,
       quantile(0.5)(@gap_expr) AS median_score_gap,
       quantile(0.9)(@gap_expr) AS p90_score_gap,
       min(b.block_timestamp) AS indexed_from,max(b.block_timestamp) AS indexed_to,
       max(s.observed_at) AS source_observed_at
@common_joins
@common_where AND s.is_winner
GROUP BY chain_id,competition_solver
ORDER BY wins_scored DESC,chain_id,competition_solver
LIMIT 2000
