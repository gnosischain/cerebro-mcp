-- FULL treasury history, one point per chain-month: each token's latest SERVED
-- snapshot in the month's last candidate days, valued with the hub price on or
-- before that day. Spot prices never enter history.
--
-- ONE scan fanned out to three grains so every chart reads the same snapshot and
-- a stacked total always equals the NAV line:
--   chain  — NAV, GNO, breadth and class counts per chain-month (all rows);
--   wallet — NAV and GNO per wallet-month (visible tokens only);
--   token  — units, price and value per priced token-month.
-- Explicit fan-out rather than GROUPING SETS: the grain is a column, no reliance
-- on grouping() or group_by_use_nulls. Chains are never blended (chain_id is in
-- every group key).
WITH @pipeline
SELECT g.1 AS grain, c.x_chain AS chain_id, toString(c.x_bucket) AS bucket,
       toString(max(c.x_date)) AS bucket_date,
       g.2 AS wallet_address,
       transform(g.2, {label_addr:Array(String)}, {label_name:Array(String)}, '')
         AS wallet_label,
       has({ltd:Array(String)}, g.2) AS is_ltd,
       g.3 AS token_address,
       if(g.1 = 'token', any(c.x_reg_symbol), '') AS registry_symbol,
       if(g.1 = 'token', any(c.x_reg_asset), '') AS asset_key,
       if(g.1 = 'token', any(c.x_reg_class), '') AS asset_class,
       if(g.1 = 'token', any(c.x_class), '') AS token_class,
       if(g.1 = 'token', sum(c.x_units), NULL) AS balance_units,
       if(g.1 = 'token', sumIf(c.x_units, NOT c.x_is_ltd), NULL) AS balance_units_ex_ltd,
       if(g.1 = 'token', any(c.x_price), NULL) AS price_usd,
       if(g.1 = 'token' AND any(c.x_price_day) IS NOT NULL,
          toString(any(c.x_price_day)), NULL) AS price_date,
       ifNull(sumIf(c.x_value, c.x_class = 'priced'), 0) AS nav_usd,
       ifNull(sumIf(c.x_value, c.x_class = 'priced' AND NOT c.x_is_ltd), 0) AS nav_usd_ex_ltd,
       ifNull(sumIf(c.x_units, c.x_is_gno), 0) AS gno_units,
       ifNull(sumIf(c.x_units, c.x_is_gno AND NOT c.x_is_ltd), 0) AS gno_units_ex_ltd,
       uniqExact(c.x_wallet) AS wallets_holding,
       uniqExactIf(c.x_token, c.x_visible) AS tokens_held,
       countIf(c.x_visible) AS positions,
       uniqExactIf(c.x_token, c.x_class = 'priced') AS priced_tokens,
       uniqExactIf(c.x_token, c.x_class = 'listed') AS listed_tokens,
       uniqExactIf(c.x_token, c.x_class = 'unverified') AS unverified_tokens,
       uniqExactIf(c.x_token, c.x_class = 'spam') AS hidden_spam_tokens
FROM classified AS c
ARRAY JOIN [('chain', '', ''), ('wallet', c.x_wallet, ''), ('token', '', c.x_token)] AS g
WHERE g.1 = 'chain' OR (g.1 = 'wallet' AND c.x_visible)
   OR (g.1 = 'token' AND c.x_class = 'priced')
GROUP BY grain, chain_id, bucket, wallet_address, token_address
ORDER BY grain, chain_id, bucket, wallet_address, token_address
