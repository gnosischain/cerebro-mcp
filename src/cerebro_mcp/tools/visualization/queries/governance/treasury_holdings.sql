-- Every token held at each chain's as-of, ALL classes (spam and retired mirrors
-- included, flagged, so the UI can reveal them with a reason and count them).
-- USD is the hub price through the registry; spot_eligible marks the only rows a
-- CoinGecko spot quote may be applied to: REVIEWED real tokens with no hub price
-- (class listed) and known decimals. Unreviewed tokens are never spot-valued — a
-- scam token can carry a CoinGecko listing, and a fabricated value is worse than
-- an honest "unpriced".
WITH @pipeline,
@rollup
SELECT ht_chain AS chain_id, ht_token AS token_address, ht_symbol AS symbol,
       ht_name AS name, ht_reg_symbol AS registry_symbol, ht_asset AS asset_key,
       ht_asset_class AS asset_class, ht_dec AS decimals, ht_meta AS metadata_status,
       ht_class AS token_class, ht_spam AS spam_reason, ht_wallets AS wallets_holding,
       toString(ht_raw) AS balance_total_raw,
       if(ht_dec IS NULL, NULL, ht_units) AS balance_units,
       if(ht_dec IS NULL, NULL, ht_units_ex) AS balance_units_ex_ltd,
       ht_share AS supply_share, ht_collisions AS symbol_collisions,
       ht_price AS price_usd,
       if(ht_price_day IS NULL, NULL, toString(ht_price_day)) AS price_date,
       if(ht_price IS NULL, '', if(ht_basis = 'proxy', 'hub_proxy', 'hub')) AS price_source,
       ht_value AS value_usd, ht_value_ex AS value_usd_ex_ltd,
       toUInt8(ht_class = 'listed' AND ht_dec IS NOT NULL) AS spot_eligible,
       toString(ht_date) AS token_date, toString(ht_as_of) AS as_of
FROM shaped
ORDER BY @sort_fragment
