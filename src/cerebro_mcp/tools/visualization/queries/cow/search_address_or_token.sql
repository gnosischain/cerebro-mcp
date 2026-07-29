SELECT chain_id,role,sum(evidence_count) AS evidence_count
FROM (
 SELECT chain_id,'owner' AS role,count() AS evidence_count FROM cow_db.orders FINAL WHERE @where AND owner={q:String} GROUP BY chain_id
 UNION ALL
 SELECT chain_id,'token',count() FROM cow_db.token_metadata FINAL WHERE @where AND token={q:String} GROUP BY chain_id
 UNION ALL
 SELECT chain_id,'settlement_executor',count() FROM cow_db.settlements WHERE @where AND solver={q:String} GROUP BY chain_id
 UNION ALL
 SELECT chain_id,'competition_solver',count() FROM cow_db.competition_solutions FINAL WHERE @where AND solver={q:String} GROUP BY chain_id
 UNION ALL
 SELECT chain_id,'competition_winner',count() FROM cow_db.solver_competitions FINAL WHERE @where AND winner={q:String} GROUP BY chain_id
 UNION ALL
 SELECT chain_id,'interaction_target',count() FROM cow_db.interactions_canonical WHERE @where AND target={q:String} GROUP BY chain_id
) GROUP BY chain_id,role HAVING evidence_count>0 ORDER BY chain_id,role
