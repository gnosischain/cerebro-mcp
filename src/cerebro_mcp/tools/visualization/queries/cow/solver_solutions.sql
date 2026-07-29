
SELECT ranking,count() AS solution_count,countIf(is_winner) AS wins,
       max(observed_at) AS source_observed_at
FROM cow_db.competition_solutions FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND solver={id:String}
GROUP BY ranking
ORDER BY ranking
