-- One token on one chain at the as-of: identity (trusted registry fields beside
-- the untrusted on-chain symbol/name), class and spam reason, the chain-wide
-- collision count, supply share and hub value. Zero rows when the treasury does
-- not hold it on the as-of day. sibling_tokens lists the same asset elsewhere
-- (from the registry) for the chain switcher.
WITH @pipeline,
@rollup
SELECT ht_chain AS chain_id, ht_token AS token_address, ht_symbol AS symbol,
       ht_name AS name, ht_reg_symbol AS registry_symbol, ht_asset AS asset_key,
       ht_asset_class AS asset_class, ht_dec AS decimals, ht_meta AS metadata_status,
       ht_class AS token_class, ht_spam AS spam_reason, ht_wallets AS wallets_holding,
       ht_collisions AS symbol_collisions, toString(ht_raw) AS balance_total_raw,
       if(ht_dec IS NULL, NULL, ht_units) AS balance_units, ht_share AS supply_share,
       ht_price AS price_usd,
       if(ht_price_day IS NULL, NULL, toString(ht_price_day)) AS price_date,
       if(ht_price IS NULL, '', if(ht_basis = 'proxy', 'hub_proxy', 'hub')) AS price_source,
       ht_value AS value_usd,
       toUInt8(ht_class = 'listed' AND ht_dec IS NOT NULL) AS spot_eligible,
       toString(ht_date) AS token_date, toString(ht_as_of) AS as_of,
       ht_block AS anchor_block, {siblings:Array(String)} AS sibling_tokens
FROM shaped
WHERE ht_token = {addr:String}
ORDER BY token_address
