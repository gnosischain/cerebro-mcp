-- One token's FULL history on one chain, split by wallet (every wallet, no top-N
-- fold): units per month and hub value where the token is priced that month.
WITH @pipeline
SELECT c.x_chain AS chain_id, toString(c.x_bucket) AS bucket,
       toString(max(c.x_date)) AS bucket_date, c.x_wallet AS wallet_address,
       transform(c.x_wallet, {label_addr:Array(String)}, {label_name:Array(String)}, '')
         AS wallet_label,
       any(c.x_is_ltd) AS is_ltd, sum(c.x_units) AS balance_units,
       sum(c.x_value) AS value_usd
FROM classified AS c
GROUP BY chain_id, bucket, wallet_address
ORDER BY bucket, wallet_address
