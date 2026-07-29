
WITH @token_metadata_cte
SELECT f.tx_hash,f.log_index,f.fee_index,f.token,
       if(tm.token='','',tm.symbol) AS token_symbol,
       toString(f.amount) AS amount_raw,
       if(tm.token='',NULL,tm.decimals) AS token_decimals,
       if(tm.token='',NULL,toFloat64(f.amount)/pow(10,toFloat64(tm.decimals))) AS amount,
       f.policy,
       multiIf(positionCaseInsensitive(f.policy,'priceImprovement')>0,'price_improvement',
               positionCaseInsensitive(f.policy,'surplus')>0,'surplus',
               positionCaseInsensitive(f.policy,'volume')>0,'volume','other') AS policy_family,
       f.source,f.observed_at AS source_observed_at
FROM cow_db.protocol_fees AS f FINAL
LEFT JOIN tm ON tm.token=f.token
WHERE f.environment={env:String} AND f.chain_id={chain_id:UInt64} AND f.order_uid={id:String}
ORDER BY f.observed_at,f.tx_hash,f.log_index,f.fee_index
