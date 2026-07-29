
SELECT auction_id,solution_index,score,ranking,is_winner,tx_hash,
       observed_at AS source_observed_at
FROM cow_db.competition_solutions FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND solver={id:String}
ORDER BY observed_at DESC,auction_id DESC,ranking
