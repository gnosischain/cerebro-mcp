SELECT max(block_timestamp)
FROM cow_db.chain_blocks
WHERE environment={env:String} AND chain_id IN (@ids)
  AND block_number IN (
    SELECT max(auction_block) FROM cow_db.solver_competitions FINAL
    WHERE environment={env:String} AND chain_id IN (@ids)
    GROUP BY chain_id
  )
