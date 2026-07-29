
WITH @token_metadata_cte, dedup AS (
  SELECT t.tx_hash AS tx_hash, t.log_index AS log_index, t.order_uid AS order_uid,
         argMax(t.block_timestamp,t.observed_at) AS block_timestamp,
         argMax(t.sell_token,t.observed_at) AS sell_token,
         argMax(t.sell_amount,t.observed_at) AS sell_amount,
         argMax(t.buy_amount,t.observed_at) AS buy_amount,
         max(t.observed_at) AS observed_at
  FROM cow_db.trades AS t
  WHERE @scope_pred AND @pair_filter
    AND t.block_timestamp IS NOT NULL AND @time_pred
  GROUP BY t.tx_hash, t.log_index, t.order_uid
), fills AS (
  SELECT d.block_timestamp, d.log_index, d.tx_hash, d.order_uid,
         if(d.sell_token={base:String},
            toFloat64(d.sell_amount)/pow(10,toFloat64(b.decimals)),
            toFloat64(d.buy_amount)/pow(10,toFloat64(b.decimals))) AS base_qty,
         if(d.sell_token={base:String},
            toFloat64(d.buy_amount)/pow(10,toFloat64(q.decimals)),
            toFloat64(d.sell_amount)/pow(10,toFloat64(q.decimals))) AS quote_qty,
         d.observed_at
  FROM dedup AS d
  INNER JOIN tm AS b ON b.token={base:String}
  INNER JOIN tm AS q ON q.token={quote:String}
), priced AS (
  SELECT *, quote_qty/nullIf(base_qty,0) AS price
  FROM fills WHERE base_qty>0 AND quote_qty>=0
)
SELECT @bucket AS bucket,
       argMin(price, tuple(block_timestamp,log_index,tx_hash,order_uid)) AS open,
       max(price) AS high, min(price) AS low,
       argMax(price, tuple(block_timestamp,log_index,tx_hash,order_uid)) AS close,
       sum(quote_qty)/nullIf(sum(base_qty),0) AS vwap,
       sum(base_qty) AS base_volume, sum(quote_qty) AS quote_volume,
       count() AS fill_count, min(block_timestamp) AS indexed_from,
       max(block_timestamp) AS indexed_to, max(observed_at) AS source_observed_at
FROM priced
GROUP BY bucket
ORDER BY bucket
