
SELECT tx_hash,log_index,
       argMax(block_number,observed_at) AS block_number,
       argMax(block_timestamp,observed_at) AS block_timestamp,
       any({id:String}) AS settlement_executor,
       max(observed_at) AS source_observed_at
FROM (
  SELECT tx_hash,log_index,block_number,block_timestamp,observed_at
  FROM cow_db.settlements
  PREWHERE solver={id:String}
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  ORDER BY block_timestamp DESC
  LIMIT @tape_arm_limit
)
GROUP BY tx_hash,log_index
ORDER BY block_timestamp DESC,log_index DESC
LIMIT @row_cap
