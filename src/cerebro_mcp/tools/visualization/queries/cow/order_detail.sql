
WITH @token_metadata_cte
SELECT o.order_uid,o.owner,o.sell_token,o.buy_token,o.receiver,
       if(s.token='','',s.symbol) AS sell_symbol,
       if(b.token='','',b.symbol) AS buy_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(o.sell_amount) AS sell_amount_raw,
       if(s.token='',NULL,toFloat64(o.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       toString(o.buy_amount) AS buy_amount_raw,
       if(b.token='',NULL,toFloat64(o.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       toString(o.fee_amount) AS fee_amount_raw,
       if(s.token='',NULL,toFloat64(o.fee_amount)/pow(10,toFloat64(s.decimals))) AS fee_amount,
       toString(o.executed_sell_amount) AS executed_sell_amount_raw,
       if(s.token='',NULL,toFloat64(o.executed_sell_amount)/pow(10,toFloat64(s.decimals))) AS executed_sell_amount,
       toString(o.executed_buy_amount) AS executed_buy_amount_raw,
       if(b.token='',NULL,toFloat64(o.executed_buy_amount)/pow(10,toFloat64(b.decimals))) AS executed_buy_amount,
       toString(o.executed_fee_amount) AS executed_fee_amount_raw,
       if(s.token='',NULL,toFloat64(o.executed_fee_amount)/pow(10,toFloat64(s.decimals))) AS executed_fee_amount,
       o.valid_to,o.kind,o.partially_fillable,o.signing_scheme,o.creation_date,o.status,o.class,
       o.app_data_hash,o.source,o.source_updated_at,o.observed_at AS source_observed_at
FROM cow_db.orders AS o FINAL
LEFT JOIN tm AS s ON s.token=o.sell_token
LEFT JOIN tm AS b ON b.token=o.buy_token
WHERE o.environment={env:String} AND o.chain_id={chain_id:UInt64} AND o.order_uid={id:String}
ORDER BY o.observed_at DESC
