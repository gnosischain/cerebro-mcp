SELECT chain_id,'transaction' AS entity_type,'transaction' AS role,sum(evidence_count) AS evidence_count
FROM (
 SELECT chain_id,count() AS evidence_count FROM cow_db.trades WHERE @where AND tx_hash={q:String} GROUP BY chain_id
 UNION ALL
 SELECT chain_id,count() FROM cow_db.settlements WHERE @where AND tx_hash={q:String} GROUP BY chain_id
 UNION ALL
 SELECT chain_id,count() FROM cow_db.competition_transactions FINAL WHERE @where AND tx_hash={q:String} GROUP BY chain_id
) GROUP BY chain_id ORDER BY chain_id
