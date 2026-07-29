
WITH @blk_cte,
sols AS (
 SELECT chain_id,auction_id,count() AS solution_count,countIf(is_winner) AS winner_rows,
        min(ranking) AS best_ranking
 FROM cow_db.competition_solutions FINAL
 WHERE @scope_pred_bare
 GROUP BY chain_id,auction_id
), txs AS (
 SELECT chain_id,auction_id,count() AS transaction_count,groupUniqArray(tx_hash) AS tx_hashes
 FROM cow_db.competition_transactions FINAL
 WHERE @scope_pred_bare
 GROUP BY chain_id,auction_id
)
SELECT c.chain_id AS chain_id,c.auction_id,b.block_timestamp AS auction_timestamp,c.auction_block,
       c.winner AS competition_winner,c.reference_score,
       coalesce(sols.solution_count,0) AS solution_count,
       coalesce(txs.transaction_count,0) AS transaction_count,txs.tx_hashes,
       b.block_timestamp AS indexed_from,b.block_timestamp AS indexed_to,
       c.observed_at AS source_observed_at
FROM cow_db.solver_competitions AS c FINAL
LEFT JOIN blk AS b ON b.chain_id=c.chain_id AND b.block_number=c.auction_block
LEFT JOIN sols ON c.chain_id=sols.chain_id AND c.auction_id=sols.auction_id
LEFT JOIN txs ON c.chain_id=txs.chain_id AND c.auction_id=txs.auction_id
WHERE @scope_pred_c AND b.block_number!=0 AND @time_pred
ORDER BY b.block_timestamp DESC,c.auction_id DESC
