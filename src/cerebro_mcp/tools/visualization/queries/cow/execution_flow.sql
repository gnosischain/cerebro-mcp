
WITH @token_metadata_cte,
exec AS (
 SELECT tx_hash,argMax(solver,tuple(block_timestamp,log_index)) AS settlement_executor
 FROM cow_db.settlements
 WHERE environment={env:String} AND chain_id={chain_id:UInt64}
   AND block_timestamp IS NOT NULL AND @flow_settle_time
 GROUP BY tx_hash
),
flows AS (
  SELECT least(t.sell_token,t.buy_token) AS token0,
         greatest(t.sell_token,t.buy_token) AS token1,
         exec.settlement_executor,count() AS fill_count,
         min(t.block_timestamp) AS indexed_from,max(t.block_timestamp) AS indexed_to,
         max(t.observed_at) AS source_observed_at
  FROM cow_db.trades AS t
  INNER JOIN exec ON exec.tx_hash=t.tx_hash
  WHERE @scope_pred_t AND t.block_timestamp IS NOT NULL AND @flow_time
    @flow_filter_sql
  GROUP BY token0,token1,settlement_executor
)
SELECT f.token0 AS token0,f.token1 AS token1,
       if(m0.token='','',m0.symbol) AS token0_symbol,
       if(m1.token='','',m1.symbol) AS token1_symbol,
       f.settlement_executor AS settlement_executor,f.fill_count AS fill_count,
       f.indexed_from AS indexed_from,f.indexed_to AS indexed_to,
       f.source_observed_at AS source_observed_at
FROM flows AS f
LEFT JOIN tm AS m0 ON m0.token=f.token0
LEFT JOIN tm AS m1 ON m1.token=f.token1
ORDER BY f.fill_count DESC,f.token0,f.token1,f.settlement_executor
