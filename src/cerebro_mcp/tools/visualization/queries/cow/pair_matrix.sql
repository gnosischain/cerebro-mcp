
WITH @token_metadata_cte,@exec_cte,
pf AS (
  SELECT least(t.sell_token,t.buy_token) AS token0,
         greatest(t.sell_token,t.buy_token) AS token1,
         exec.settlement_executor AS settlement_executor,
         count() AS fill_count,
         min(t.block_timestamp) AS indexed_from, max(t.block_timestamp) AS indexed_to,
         max(t.observed_at) AS source_observed_at
  FROM cow_db.trades AS t
  INNER JOIN exec ON exec.tx_hash=t.tx_hash
  WHERE @trade_where
  GROUP BY token0, token1, settlement_executor
),
tp AS (
  SELECT token0, token1 FROM pf GROUP BY token0, token1
  ORDER BY sum(fill_count) DESC LIMIT 30
)
SELECT p.token0 AS token0, p.token1 AS token1,
       if(m0.token='','',m0.symbol) AS token0_symbol,
       if(m1.token='','',m1.symbol) AS token1_symbol,
       p.settlement_executor AS settlement_executor,
       p.fill_count AS fill_count,
       p.fill_count/sum(p.fill_count) OVER (PARTITION BY p.token0,p.token1) AS pair_share,
       p.indexed_from AS indexed_from, p.indexed_to AS indexed_to,
       p.source_observed_at AS source_observed_at
FROM pf AS p
INNER JOIN tp USING (token0, token1)
LEFT JOIN tm AS m0 ON m0.token=p.token0
LEFT JOIN tm AS m1 ON m1.token=p.token1
ORDER BY p.token0, p.token1, p.fill_count DESC
LIMIT 1000
