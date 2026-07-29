
WITH o AS (
  SELECT * FROM cow_db.orders FINAL
  WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND order_uid={id:String}
  LIMIT 1
)
SELECT
 (SELECT count() FROM cow_db.trades
  WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND order_uid={id:String}) AS fills,
 any(o.kind) AS kind,
 @realized_surplus AS realized_surplus_bps,
 if(any(o.kind)='buy',
    toFloat64(any(o.executed_buy_amount))/nullIf(toFloat64(any(o.buy_amount)),0),
    toFloat64(any(o.executed_sell_amount))/nullIf(toFloat64(any(o.sell_amount)),0)) AS fill_ratio,
 any(o.creation_date) AS creation_date,
 (SELECT min(block_timestamp) FROM cow_db.trades
  WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND order_uid={id:String}) AS first_fill_at,
 max(o.observed_at) AS source_observed_at
FROM o
ORDER BY fills
