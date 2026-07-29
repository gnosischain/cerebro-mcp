
SELECT order_uid,payload,observed_at AS source_observed_at
FROM cow_db.auction_orders FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND auction_id={id:UInt64}
ORDER BY order_uid
