
SELECT c.auction_id,c.winner AS competition_winner,c.reference_score,c.auction_block,
       nullIf(b.block_timestamp,toDateTime(0)) AS auction_timestamp,
       c.source,c.observed_at AS source_observed_at
FROM cow_db.solver_competitions AS c FINAL
LEFT JOIN (
  SELECT chain_id, block_number, argMax(block_timestamp, observed_at) AS block_timestamp
  FROM cow_db.chain_blocks
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND block_number IN (SELECT auction_block FROM cow_db.solver_competitions FINAL
                         WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND auction_id={id:UInt64})
  GROUP BY chain_id, block_number
) AS b ON b.chain_id=c.chain_id AND b.block_number=c.auction_block
WHERE c.environment={env:String} AND c.chain_id={chain_id:UInt64} AND c.auction_id={id:UInt64}
ORDER BY c.observed_at DESC
