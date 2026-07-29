
SELECT ct.auction_id,ct.tx_index,c.winner AS competition_winner,c.reference_score,
       c.auction_block,ws.solver AS winning_solution_solver,
       ws.tx_hash AS solution_tx_hash,ws.solution_index,
       greatest(ct.observed_at,c.observed_at,ws.observed_at) AS source_observed_at
FROM cow_db.competition_transactions AS ct FINAL
LEFT JOIN cow_db.solver_competitions AS c FINAL
 ON ct.environment=c.environment AND ct.chain_id=c.chain_id AND ct.auction_id=c.auction_id
LEFT JOIN cow_db.competition_solutions AS ws FINAL
 ON ct.environment=ws.environment AND ct.chain_id=ws.chain_id
 AND ct.auction_id=ws.auction_id AND ws.is_winner
WHERE ct.environment={env:String} AND ct.chain_id={chain_id:UInt64} AND ct.tx_hash={id:String}
ORDER BY ct.auction_id,ct.tx_index,ws.solution_index
