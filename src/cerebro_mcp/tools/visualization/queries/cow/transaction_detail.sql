
WITH comp AS (
 SELECT auction_id FROM cow_db.competition_transactions FINAL
 WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND tx_hash={id:String}
)
SELECT s.tx_hash,s.block_number,s.block_hash,s.block_timestamp,
       s.solver AS settlement_executor,s.log_index,comp.auction_id,
       s.observed_at AS source_observed_at
FROM cow_db.settlements_canonical s
LEFT JOIN comp ON 1
WHERE s.environment={env:String} AND s.chain_id={chain_id:UInt64} AND s.tx_hash={id:String}
ORDER BY s.log_index
