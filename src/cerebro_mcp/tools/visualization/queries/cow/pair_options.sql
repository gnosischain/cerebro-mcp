
WITH @token_metadata_cte,
p AS (
  SELECT least(sell_token,buy_token) AS token0,greatest(sell_token,buy_token) AS token1,
         uniq((tx_hash,log_index,order_uid)) AS fill_count,
         min(block_timestamp) AS indexed_from,max(block_timestamp) AS indexed_to,
         max(observed_at) AS source_observed_at
  FROM cow_db.trades
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND sell_token != buy_token AND block_timestamp IS NOT NULL
    AND block_timestamp >= now() - INTERVAL 30 DAY
  GROUP BY token0,token1
  ORDER BY fill_count DESC,token0,token1
  LIMIT 50
)
SELECT p.token0 AS token0,p.token1 AS token1,
       if(m0.token='','',m0.symbol) AS token0_symbol,
       if(m1.token='','',m1.symbol) AS token1_symbol,
       p.fill_count AS fill_count,
       p.indexed_from AS indexed_from,p.indexed_to AS indexed_to,
       p.source_observed_at AS source_observed_at
FROM p
LEFT JOIN tm AS m0 ON m0.token=p.token0
LEFT JOIN tm AS m1 ON m1.token=p.token1
ORDER BY p.fill_count DESC,p.token0,p.token1
