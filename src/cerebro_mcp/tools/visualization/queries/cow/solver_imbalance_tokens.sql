
WITH @token_metadata_cte,@accounting_ctes
SELECT f.token AS token,
       if(tm.token='','',tm.symbol) AS token_symbol,
       if(any(tm.token)='',NULL,any(tm.decimals)) AS token_decimals,
       count() AS settlements,
       toString(sum(f.net_atoms)) AS net_amount_raw,
       if(any(tm.token)='',NULL,
          toFloat64(sum(f.net_atoms))/pow(10,toFloat64(any(tm.decimals)))) AS net_amount,
       sum(if(toFloat64(pr.price)>0,
              toFloat64(f.net_atoms)*toFloat64(pr.price)/1e18, 0)) AS net_native_wei_known,
       max(f.block_timestamp) AS source_observed_at
FROM flows AS f
LEFT JOIN am ON am.tx_hash=f.tx_hash
LEFT JOIN pr ON pr.auction_id=am.auction_id AND pr.token=f.token
LEFT JOIN tm ON tm.token=f.token
GROUP BY f.token, token_symbol
ORDER BY abs(sum(if(toFloat64(pr.price)>0,toFloat64(f.net_atoms)*toFloat64(pr.price)/1e18,0))) DESC, token
LIMIT 200
