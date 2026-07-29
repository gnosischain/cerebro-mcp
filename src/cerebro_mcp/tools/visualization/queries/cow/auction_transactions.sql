
SELECT tx_index,tx_hash,source,observed_at AS source_observed_at
FROM cow_db.competition_transactions FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND auction_id={id:UInt64}
ORDER BY tx_index,tx_hash
