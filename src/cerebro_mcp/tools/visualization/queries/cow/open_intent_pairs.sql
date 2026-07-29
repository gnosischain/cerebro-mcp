
WITH @token_cte
SELECT p.token0 AS token0,p.token1 AS token1,
       if(m0.token='','',m0.symbol) AS token0_symbol,
       if(m1.token='','',m1.symbol) AS token1_symbol,
       p.open_orders AS open_orders,
       p.obs AS source_observed_at
FROM (
  SELECT least(sell_token,buy_token) AS token0,
         greatest(sell_token,buy_token) AS token1,
         count() AS open_orders,max(obs_at) AS obs
  FROM (
    SELECT order_uid,valid_to,
           argMax(sell_token,observed_at) AS sell_token,
           argMax(buy_token,observed_at) AS buy_token,
           argMax(status,observed_at) AS status,
           max(observed_at) AS obs_at
    FROM cow_db.orders
    WHERE environment={env:String} AND chain_id={chain_id:UInt64}
      AND valid_to>toUnixTimestamp(now())
    GROUP BY order_uid,valid_to
  )
  WHERE status='open'
  GROUP BY token0,token1
) AS p
LEFT JOIN tm AS m0 ON m0.token=p.token0
LEFT JOIN tm AS m1 ON m1.token=p.token1
ORDER BY p.open_orders DESC,p.token0,p.token1
LIMIT 30
