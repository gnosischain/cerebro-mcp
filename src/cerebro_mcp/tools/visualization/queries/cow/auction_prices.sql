
WITH @token_metadata_cte
SELECT p.token,
       if(tm.token='','',tm.symbol) AS token_symbol,
       toString(p.price) AS price_raw,p.observed_at AS source_observed_at
FROM cow_db.auction_prices AS p FINAL
LEFT JOIN tm ON tm.token=p.token
WHERE p.environment={env:String} AND p.chain_id={chain_id:UInt64} AND p.auction_id={id:UInt64}
ORDER BY p.token
