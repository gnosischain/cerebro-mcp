
exec AS (
  SELECT tx_hash, argMax(solver,tuple(block_timestamp,log_index)) AS settlement_executor
  FROM cow_db.settlements
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND block_timestamp IS NOT NULL AND @settle_time
  GROUP BY tx_hash
)
