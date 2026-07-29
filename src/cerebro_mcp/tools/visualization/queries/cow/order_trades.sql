
WITH @token_metadata_cte
SELECT t.block_timestamp,t.tx_hash,t.log_index,t.owner,t.sell_token,t.buy_token,
       if(s.token='','',s.symbol) AS sell_symbol,
       if(b.token='','',b.symbol) AS buy_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(t.sell_amount) AS sell_amount_raw,
       if(s.token='',NULL,toFloat64(t.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       toString(t.buy_amount) AS buy_amount_raw,
       if(b.token='',NULL,toFloat64(t.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       toString(t.fee_amount) AS fee_amount_raw,
       if(s.token='',NULL,toFloat64(t.fee_amount)/pow(10,toFloat64(s.decimals))) AS fee_amount,
       t.source,t.observed_at AS source_observed_at
FROM cow_db.trades AS t
LEFT JOIN tm AS s ON s.token=t.sell_token
LEFT JOIN tm AS b ON b.token=t.buy_token
WHERE t.environment={env:String} AND t.chain_id={chain_id:UInt64} AND t.order_uid={id:String}
ORDER BY t.block_timestamp DESC,t.log_index DESC,t.tx_hash DESC
