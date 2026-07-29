
WITH @token_metadata_cte, cp AS (
  SELECT argMax(block_number,updated_at) AS b
  FROM cow_db.indexing_checkpoints
  WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND source='rpc'
)
SELECT u.block_timestamp,u.tx_hash,u.log_index,u.order_uid,u.sell_token,u.buy_token,
       if(s.token='','',s.symbol) AS sell_symbol,
       if(b.token='','',b.symbol) AS buy_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(u.sell_amount) AS sell_amount_raw,
       if(s.token='',NULL,toFloat64(u.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       toString(u.buy_amount) AS buy_amount_raw,
       if(b.token='',NULL,toFloat64(u.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       u.obs_at AS source_observed_at
FROM (
  SELECT tx_hash,log_index,order_uid,
         argMax(block_timestamp,observed_at) AS block_timestamp,
         argMax(sell_token,observed_at) AS sell_token,
         argMax(buy_token,observed_at) AS buy_token,
         argMax(sell_amount,observed_at) AS sell_amount,
         argMax(buy_amount,observed_at) AS buy_amount,
         max(observed_at) AS obs_at
  FROM (
    SELECT t.tx_hash,t.log_index,t.order_uid,t.block_timestamp,
           t.sell_token,t.buy_token,t.sell_amount,t.buy_amount,t.observed_at
    FROM cow_db.trades AS t
    PREWHERE t.owner={id:String}
    WHERE t.environment={env:String} AND t.chain_id={chain_id:UInt64}
      AND t.block_number<=(SELECT b FROM cp)
    ORDER BY t.block_timestamp DESC
    LIMIT @tape_arm_limit
  )
  GROUP BY tx_hash,log_index,order_uid
  ORDER BY block_timestamp DESC
  LIMIT @row_cap
) AS u
LEFT JOIN tm AS s ON s.token=u.sell_token
LEFT JOIN tm AS b ON b.token=u.buy_token
ORDER BY u.block_timestamp DESC,u.log_index DESC
