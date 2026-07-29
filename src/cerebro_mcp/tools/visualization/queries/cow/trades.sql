
WITH @tmx
SELECT u.block_ts AS block_timestamp,u.chain_id AS chain_id,
       u.tx_hash,u.log_index,u.order_uid,u.owner,
       u.sell_token,if(s.token='','',s.symbol) AS sell_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       toString(u.sell_amount) AS sell_amount_raw,
       if(s.token='',NULL,toFloat64(u.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       u.buy_token,if(b.token='','',b.symbol) AS buy_symbol,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(u.buy_amount) AS buy_amount_raw,
       if(b.token='',NULL,toFloat64(u.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       u.obs_at AS source_observed_at
FROM (
  SELECT chain_id,tx_hash,log_index,order_uid,
         argMax(block_timestamp,observed_at) AS block_ts,
         argMax(owner,observed_at) AS owner,
         argMax(sell_token,observed_at) AS sell_token,
         argMax(buy_token,observed_at) AS buy_token,
         argMax(sell_amount,observed_at) AS sell_amount,
         argMax(buy_amount,observed_at) AS buy_amount,
         max(observed_at) AS obs_at
  FROM cow_db.trades
  WHERE @feed_pred
    AND block_timestamp >= @live_window
  GROUP BY chain_id,tx_hash,log_index,order_uid
  ORDER BY block_ts DESC,log_index DESC
  LIMIT 50
) AS u
LEFT JOIN tmx AS s ON s.chain_id=u.chain_id AND s.token=u.sell_token
LEFT JOIN tmx AS b ON b.chain_id=u.chain_id AND b.token=u.buy_token
ORDER BY u.block_ts DESC,u.log_index DESC
LIMIT 50
