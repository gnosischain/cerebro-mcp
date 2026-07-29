
WITH @token_cte, open_orders AS (
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
 FROM (
   -- argMax dedup replaces FINAL (whole-chain k-way merge of the ~millions-row
   -- backfilled table). Pair + unexpired-validity prefilters are IMMUTABLE per
   -- order_uid, so the hash holds only this pair's live-validity orders; the
   -- immutable columns ride the GROUP BY key (explicit list — a qualified
   -- asterisk through aggregation loses names, code 47). Mutable status is
   -- filtered via HAVING; max(observed_at) must NOT self-alias to observed_at
   -- beside sibling argMax(x,observed_at) (code 184) — downstream reads obs_at.
   SELECT order_uid,owner,kind,class,partially_fillable,creation_date,valid_to,
          sell_token,buy_token,sell_amount,buy_amount,
          argMax(status,observed_at) AS st,
          argMax(executed_sell_amount,observed_at) AS executed_sell_amount,
          argMax(executed_buy_amount,observed_at) AS executed_buy_amount,
          max(observed_at) AS obs_at
   FROM cow_db.orders
   WHERE environment={env:String} AND chain_id={chain_id:UInt64}
     AND valid_to>toUnixTimestamp(parseDateTime64BestEffort({server_as_of:String}))
     AND ((sell_token={base:String} AND buy_token={quote:String})
          OR (sell_token={quote:String} AND buy_token={base:String}))
   GROUP BY order_uid,owner,kind,class,partially_fillable,creation_date,valid_to,
            sell_token,buy_token,sell_amount,buy_amount
   HAVING st='open'
 ) AS o
), enriched AS (
 SELECT o.*,
   if(s.token='','',s.symbol) AS sell_symbol,
   if(b.token='','',b.symbol) AS buy_symbol,
   if(s.token='',NULL,s.decimals) AS sell_decimals,
   if(b.token='',NULL,b.decimals) AS buy_decimals,
   if(s.token='',NULL,remaining_sell_float/pow(10,toFloat64(s.decimals))) AS remaining_sell,
   if(b.token='',NULL,remaining_buy_float/pow(10,toFloat64(b.decimals))) AS remaining_buy,
   if(o.sell_token={base:String},'ask','bid') AS side
 FROM open_orders o
 LEFT JOIN tm s ON s.token=o.sell_token
 LEFT JOIN tm b ON b.token=o.buy_token
 WHERE remaining_sell_float>0 AND remaining_buy_float>0
), priced AS (
 SELECT *, class AS order_class,
   if(side='ask',remaining_buy/nullIf(remaining_sell,0),
                 remaining_sell/nullIf(remaining_buy,0)) AS price,
   if(side='ask',remaining_sell,remaining_buy) AS amount_base,
   if(side='ask',remaining_buy,remaining_sell) AS amount_quote,
   toString(sell_amount) AS sell_amount_raw,
   toString(buy_amount) AS buy_amount_raw,
   obs_at AS source_observed_at
 FROM enriched
 WHERE sell_decimals IS NOT NULL AND buy_decimals IS NOT NULL
)
@ladder_projection
