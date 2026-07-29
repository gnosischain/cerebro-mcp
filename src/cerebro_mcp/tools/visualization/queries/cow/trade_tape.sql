WITH @shared_ctes,@tmx
SELECT u.block_timestamp AS block_timestamp,u.chain_id AS chain_id,u.tx_hash AS tx_hash,
       u.log_index AS log_index,u.order_uid AS order_uid,u.owner AS owner,
       u.sell_token AS sell_token,if(s.symbol='',u.sell_token,s.symbol) AS sell_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       toString(u.sell_amount) AS sell_amount_raw,
       if(s.symbol='',NULL,toFloat64(u.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       u.buy_token AS buy_token,if(b.symbol='',u.buy_token,b.symbol) AS buy_symbol,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(u.buy_amount) AS buy_amount_raw,
       if(b.symbol='',NULL,toFloat64(u.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       toString(u.fee_amount) AS fee_amount_raw,
       if(s.token='',NULL,toFloat64(u.fee_amount)/pow(10,toFloat64(s.decimals))) AS fee_amount,
       u.source AS source,u.obs_at AS source_observed_at
FROM (@deduped_tape) AS u
LEFT JOIN tmx AS s ON s.chain_id=u.chain_id AND s.token=u.sell_token
LEFT JOIN tmx AS b ON b.chain_id=u.chain_id AND b.token=u.buy_token
ORDER BY u.block_timestamp DESC,u.log_index DESC,u.tx_hash DESC,u.order_uid DESC
