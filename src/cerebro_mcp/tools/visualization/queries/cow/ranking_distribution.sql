
WITH @blocks_cte
SELECT s.ranking,count() AS solution_count,countIf(s.is_winner) AS winners,
       min(b.block_timestamp) AS indexed_from,max(b.block_timestamp) AS indexed_to,
       max(s.observed_at) AS source_observed_at
@common
GROUP BY s.ranking
ORDER BY s.ranking
