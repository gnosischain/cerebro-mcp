
SELECT least(sell_token,buy_token) AS token0,
       greatest(sell_token,buy_token) AS token1, count() AS fills
FROM cow_db.trades
WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  AND sell_token != buy_token
GROUP BY token0, token1
ORDER BY fills DESC, token0, token1
LIMIT 1
