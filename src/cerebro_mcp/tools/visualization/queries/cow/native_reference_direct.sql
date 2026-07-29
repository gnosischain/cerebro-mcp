
WITH @token_cte
SELECT observed_at AS bucket, toFloat64OrNull(native_price)*@decimal_factor AS price,
       observed_at AS indexed_from, observed_at AS indexed_to, observed_at AS source_observed_at
FROM cow_db.native_prices FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND token={base:String}
  AND @native_time
ORDER BY observed_at
