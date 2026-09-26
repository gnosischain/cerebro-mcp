-- One wallet's positions on one chain at the as-of, ALL classes (flagged). The
-- collision count and the token's wallet count are chain-wide (window over every
-- wallet BEFORE the wallet filter); treasury_share is this wallet's share of the
-- treasury's own position — a contract returning the same balance to every
-- caller shows up as 1/n for all n wallets.
WITH @pipeline,
tagged AS (
  SELECT c.*,
         uniqExact(c.x_token) OVER (PARTITION BY c.x_chain,
           upper(replaceRegexpAll(ifNull(c.x_symbol, ''), '[^A-Za-z0-9.]', ''))) - 1
           AS x_collisions
  FROM classified AS c
)
SELECT t.x_chain AS chain_id, t.x_token AS token_address,
       leftUTF8(ifNull(t.x_symbol, ''), 64) AS symbol,
       leftUTF8(ifNull(t.x_name, ''), 96) AS name,
       t.x_reg_symbol AS registry_symbol, t.x_reg_asset AS asset_key,
       t.x_reg_class AS asset_class, t.x_dec AS decimals,
       t.x_meta_status AS metadata_status, t.x_class AS token_class,
       t.x_spam AS spam_reason, t.x_wallets AS wallets_holding,
       t.x_collisions AS symbol_collisions,
       toString(t.x_raw) AS balance_total_raw, t.x_units AS balance_units,
       toFloat64(t.x_raw) / nullIf(toFloat64(t.x_tok_raw), 0) AS treasury_share,
       if(ifNull(t.x_supply, 0) = 0, NULL,
          toFloat64(t.x_tok_raw) / toFloat64(t.x_supply)) AS supply_share,
       t.x_price AS price_usd,
       if(t.x_price_day IS NULL, NULL, toString(t.x_price_day)) AS price_date,
       if(t.x_price IS NULL, '', if(t.x_basis = 'proxy', 'hub_proxy', 'hub')) AS price_source,
       t.x_value AS value_usd,
       toUInt8(t.x_class = 'listed' AND t.x_dec IS NOT NULL) AS spot_eligible,
       toString(t.x_date) AS token_date, toString(t.x_as_of) AS as_of
FROM tagged AS t
WHERE t.x_wallet = {addr:String}
ORDER BY (t.x_class IN ('spam', 'retired_mirror')) ASC, value_usd DESC NULLS LAST,
         t.x_token
