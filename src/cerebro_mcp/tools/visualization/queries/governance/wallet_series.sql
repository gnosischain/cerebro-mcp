-- One wallet's FULL history on one chain: priced registry tokens per month, units
-- and hub value on each token's served month-end day. The served lookup is
-- restricted to registry tokens (the only rows this series can value).
WITH @pipeline
SELECT c.x_chain AS chain_id, toString(c.x_bucket) AS bucket,
       toString(max(c.x_date)) AS bucket_date, c.x_token AS token_address,
       any(c.x_reg_symbol) AS registry_symbol, any(c.x_reg_asset) AS asset_key,
       any(c.x_reg_class) AS asset_class, any(c.x_class) AS token_class,
       sum(c.x_units) AS balance_units, any(c.x_price) AS price_usd,
       if(any(c.x_price_day) IS NULL, NULL, toString(any(c.x_price_day))) AS price_date,
       sum(c.x_value) AS value_usd
FROM classified AS c
WHERE c.x_class = 'priced'
GROUP BY chain_id, bucket, token_address
ORDER BY bucket, token_address
