
FROM cow_db.trades AS t
INNER JOIN (
  SELECT order_uid,
         argMax(sell_amount,observed_at) AS sell_amount,
         argMax(buy_amount,observed_at) AS buy_amount,
         argMax(creation_date,observed_at) AS creation_date
  FROM cow_db.orders
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  GROUP BY order_uid
) AS o ON o.order_uid=t.order_uid
WHERE t.environment={env:String} AND t.chain_id={chain_id:UInt64}
  AND t.block_number<=(
    SELECT argMax(block_number,updated_at) FROM cow_db.indexing_checkpoints
    WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND source='rpc')
  AND t.block_timestamp IS NOT NULL
