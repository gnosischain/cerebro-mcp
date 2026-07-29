
SELECT block_timestamp,log_index,target,toString(value) AS value_raw,selector,
       observed_at AS source_observed_at
FROM cow_db.interactions_canonical
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND tx_hash={id:String}
ORDER BY log_index,target
