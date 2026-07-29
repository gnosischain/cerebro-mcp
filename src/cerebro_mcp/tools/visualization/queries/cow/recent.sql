
WITH @token_metadata_cte, cp AS (
  SELECT argMax(block_number,updated_at) AS b
  FROM cow_db.indexing_checkpoints
  WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND source='rpc'
)
SELECT u.block_timestamp AS block_timestamp, u.tx_hash AS tx_hash, u.order_uid AS order_uid,
       u.log_index AS log_index, u.owner AS owner,
       u.sell_token AS sell_token, if(s.token='',u.sell_token,s.symbol) AS sell_symbol,
       if(s.token='',NULL,s.decimals) AS sell_decimals,
       toString(u.sell_amount) AS sell_amount_raw,
       if(s.token='',NULL,toFloat64(u.sell_amount)/pow(10,toFloat64(s.decimals))) AS sell_amount,
       u.buy_token AS buy_token, if(b.token='',u.buy_token,b.symbol) AS buy_symbol,
       if(b.token='',NULL,b.decimals) AS buy_decimals,
       toString(u.buy_amount) AS buy_amount_raw,
       if(b.token='',NULL,toFloat64(u.buy_amount)/pow(10,toFloat64(b.decimals))) AS buy_amount,
       toString(u.fee_amount) AS fee_amount_raw,
       if(s.token='',NULL,toFloat64(u.fee_amount)/pow(10,toFloat64(s.decimals))) AS fee_amount,
       u.source AS source,
       u.obs_at AS source_observed_at
FROM (
  SELECT tx_hash,log_index,order_uid,
         argMax(block_timestamp,observed_at) AS block_timestamp,
         argMax(owner,observed_at) AS owner,
         argMax(sell_token,observed_at) AS sell_token,
         argMax(buy_token,observed_at) AS buy_token,
         argMax(sell_amount,observed_at) AS sell_amount,
         argMax(buy_amount,observed_at) AS buy_amount,
         argMax(fee_amount,observed_at) AS fee_amount,
         argMax(source,observed_at) AS source,
         max(observed_at) AS obs_at
  FROM (
    SELECT t.tx_hash,t.log_index,t.order_uid,t.block_timestamp,t.owner,
           t.sell_token,t.buy_token,t.sell_amount,t.buy_amount,t.fee_amount,
           t.source,t.observed_at
    FROM cow_db.trades AS t
    WHERE @scope_pred AND @pair_filter
      AND t.block_number<=(SELECT b FROM cp)
      AND t.block_timestamp IS NOT NULL AND @time_pred
    ORDER BY t.block_timestamp DESC
    LIMIT @tape_arm_limit
  )
  GROUP BY tx_hash,log_index,order_uid
  ORDER BY block_timestamp DESC
  LIMIT @row_cap
) AS u
LEFT JOIN tm AS s ON s.token=u.sell_token
LEFT JOIN tm AS b ON b.token=u.buy_token
ORDER BY u.block_timestamp DESC, u.log_index DESC, u.tx_hash DESC, u.order_uid DESC
