SELECT max(block_timestamp) FROM cow_db.chain_blocks
WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  AND block_number IN (
    SELECT argMax(auction_block, observed_at) FROM cow_db.solver_competitions FINAL
    WHERE environment={env:String} AND chain_id={chain_id:UInt64} GROUP BY auction_id)
