
SELECT observed_at,native_price,source,observed_at AS indexed_from,
       observed_at AS indexed_to,observed_at AS source_observed_at
FROM cow_db.native_prices FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND token={id:String}
ORDER BY observed_at
