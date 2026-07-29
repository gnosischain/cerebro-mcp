
SELECT solution_index,solver AS competition_solver,score,ranking,is_winner,tx_hash,
       payload,observed_at AS source_observed_at
FROM cow_db.competition_solutions FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND auction_id={id:UInt64}
ORDER BY ranking,solution_index
