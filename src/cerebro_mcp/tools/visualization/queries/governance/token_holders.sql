-- Which treasury wallets hold one token on one chain at the as-of, with labels.
-- treasury_share is each wallet's share of the treasury's own position.
WITH @pipeline
SELECT c.x_chain AS chain_id, c.x_wallet AS wallet_address,
       transform(c.x_wallet, {label_addr:Array(String)}, {label_name:Array(String)}, '')
         AS wallet_label,
       {label_source:String} AS label_source, c.x_is_ltd AS is_ltd,
       toString(c.x_raw) AS balance_total_raw, c.x_units AS balance_units,
       c.x_value AS value_usd,
       toFloat64(c.x_raw) / nullIf(toFloat64(c.x_tok_raw), 0) AS treasury_share
FROM classified AS c
WHERE c.x_token = {addr:String}
ORDER BY c.x_raw DESC, wallet_address
