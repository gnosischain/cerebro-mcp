
WITH @tmx
SELECT u.order_uid,u.chain_id AS chain_id,u.owner,u.kind,u.st AS status,u.creation_date,u.valid_to,
       u.partially_fillable,
       u.sell_token,if(s.token='','',s.symbol) AS sell_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       toString(u.sell_amount) AS sell_amount_raw,
       if(s.token='',NULL,toFloat64(u.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       u.buy_token,if(b.token='','',b.symbol) AS buy_symbol,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(u.buy_amount) AS buy_amount_raw,
       if(b.token='',NULL,toFloat64(u.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       if(u.sell_amount>0,
          least(1,toFloat64(u.exec_sell)/toFloat64(u.sell_amount)),0) AS fill_ratio,
       u.obs_at AS source_observed_at
FROM (
  SELECT chain_id,order_uid,creation_date,valid_to,
         argMax(status,observed_at) AS st,
         argMax(owner,observed_at) AS owner,
         argMax(kind,observed_at) AS kind,
         argMax(partially_fillable,observed_at) AS partially_fillable,
         argMax(sell_token,observed_at) AS sell_token,
         argMax(buy_token,observed_at) AS buy_token,
         argMax(sell_amount,observed_at) AS sell_amount,
         argMax(buy_amount,observed_at) AS buy_amount,
         argMax(executed_sell_amount,observed_at) AS exec_sell,
         max(observed_at) AS obs_at
  FROM cow_db.orders
  WHERE @feed_pred
    AND valid_to>toUnixTimestamp(now())
  GROUP BY chain_id,order_uid,creation_date,valid_to
  HAVING st='open'
  ORDER BY creation_date DESC,order_uid DESC
  LIMIT 100
) AS u
LEFT JOIN tmx AS s ON s.chain_id=u.chain_id AND s.token=u.sell_token
LEFT JOIN tmx AS b ON b.chain_id=u.chain_id AND b.token=u.buy_token
ORDER BY u.creation_date DESC,u.order_uid DESC
LIMIT 100
