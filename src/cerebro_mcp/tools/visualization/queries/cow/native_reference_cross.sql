
WITH @token_cte, bp AS (
  SELECT toStartOfMinute(observed_at) AS bucket,
         argMax(toFloat64OrNull(native_price),observed_at) AS base_native_price,
         max(observed_at) AS base_observed_at
  FROM cow_db.native_prices FINAL
  WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND token={base:String}
    AND @native_time
  GROUP BY bucket
), qp AS (
  SELECT toStartOfMinute(observed_at) AS bucket,
         argMax(toFloat64OrNull(native_price),observed_at) AS quote_native_price,
         max(observed_at) AS quote_observed_at
  FROM cow_db.native_prices FINAL
  WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND token={quote:String}
    AND @native_time
  GROUP BY bucket
)
SELECT bp.bucket, bp.base_native_price/nullIf(qp.quote_native_price,0)*@decimal_factor AS price,
       least(bp.base_observed_at,qp.quote_observed_at) AS indexed_from,
       greatest(bp.base_observed_at,qp.quote_observed_at) AS indexed_to,
       greatest(bp.base_observed_at,qp.quote_observed_at) AS source_observed_at
FROM bp INNER JOIN qp USING bucket
ORDER BY bucket
