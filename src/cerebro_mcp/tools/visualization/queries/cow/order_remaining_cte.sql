
@token_cte, open_orders AS (
 SELECT o.*,
   if(o.executed_sell_amount<o.sell_amount,
      toUInt256(o.sell_amount-o.executed_sell_amount),toUInt256(0)) AS residual_sell_raw,
   if(o.executed_buy_amount<o.buy_amount,
      toUInt256(o.buy_amount-o.executed_buy_amount),toUInt256(0)) AS residual_buy_raw,
   if(o.kind='buy',
      toFloat64(o.sell_amount)*toFloat64(residual_buy_raw)
        /nullIf(toFloat64(o.buy_amount),0),
      toFloat64(residual_sell_raw)) AS remaining_sell_float,
   if(o.kind='buy',
      toFloat64(residual_buy_raw),
      toFloat64(o.buy_amount)*toFloat64(residual_sell_raw)
        /nullIf(toFloat64(o.sell_amount),0)) AS remaining_buy_float
 FROM cow_db.orders AS o FINAL
 WHERE environment={env:String} AND chain_id={chain_id:UInt64}
   AND o.status='open' AND o.valid_to>toUnixTimestamp(parseDateTime64BestEffort({server_as_of:String}))
   AND o.status!='presignaturePending'
   @owner_predicate
   AND ((o.sell_token={base:String} AND o.buy_token={quote:String})
        OR (o.sell_token={quote:String} AND o.buy_token={base:String}))
), enriched AS (
 SELECT o.*,
   if(s.token='','',s.symbol) AS sell_symbol,
   if(b.token='','',b.symbol) AS buy_symbol,
   if(s.token='',NULL,s.decimals) AS sell_decimals,
   if(b.token='',NULL,b.decimals) AS buy_decimals,
   if(s.token='',NULL,toFloat64(o.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount_normalized,
   if(b.token='',NULL,toFloat64(o.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount_normalized,
   if(s.token='',NULL,remaining_sell_float/pow(10,toFloat64(s.decimals))) AS remaining_sell,
   if(b.token='',NULL,remaining_buy_float/pow(10,toFloat64(b.decimals))) AS remaining_buy,
   if(o.sell_token={base:String},'ask','bid') AS side,
   if(s.token='' OR b.token='',NULL,if(o.sell_token={base:String},
      (remaining_buy_float/pow(10,toFloat64(b.decimals)))
        /nullIf(remaining_sell_float/pow(10,toFloat64(s.decimals)),0),
      (remaining_sell_float/pow(10,toFloat64(s.decimals)))
        /nullIf(remaining_buy_float/pow(10,toFloat64(b.decimals)),0))) AS limit_price,
   if(s.token='' OR b.token='',NULL,if(o.sell_token={base:String},
      remaining_sell_float/pow(10,toFloat64(s.decimals)),
      remaining_buy_float/pow(10,toFloat64(b.decimals)))) AS base_remaining
 FROM open_orders o
 LEFT JOIN tm s ON s.token=o.sell_token
 LEFT JOIN tm b ON b.token=o.buy_token
 WHERE remaining_sell_float>0 AND remaining_buy_float>0
), normalized AS (
 SELECT * FROM enriched
 WHERE sell_decimals IS NOT NULL AND buy_decimals IS NOT NULL
)
