-- Per (chain, token) roll-up of the classified as-of rows, over ALL wallets, then
-- the chain-wide symbol-collision count (how many OTHER held tokens fold to the
-- same ASCII symbol — the honest answer to "is this the real USDC": the symbol
-- identifies nothing). Callers filter to one token AFTER this, so collisions and
-- supply shares mean the same thing on the holdings table and the token page.
tok AS (
  SELECT c.x_chain AS ht_chain, c.x_token AS ht_token,
         leftUTF8(ifNull(any(c.x_symbol), ''), 64) AS ht_symbol,
         leftUTF8(ifNull(any(c.x_name), ''), 96) AS ht_name,
         any(c.x_reg_symbol) AS ht_reg_symbol, any(c.x_reg_asset) AS ht_asset,
         any(c.x_reg_class) AS ht_asset_class, any(c.x_dec) AS ht_dec,
         any(c.x_meta_status) AS ht_meta, any(c.x_class) AS ht_class,
         any(c.x_spam) AS ht_spam, count() AS ht_wallets,
         sum(c.x_raw) AS ht_raw, sum(c.x_units) AS ht_units,
         sumIf(c.x_units, NOT c.x_is_ltd) AS ht_units_ex,
         any(c.x_tok_raw) AS ht_tok_raw, any(c.x_supply) AS ht_supply,
         any(c.x_price) AS ht_price, any(c.x_price_day) AS ht_price_day,
         any(c.x_basis) AS ht_basis, sum(c.x_value) AS ht_value,
         sumIf(c.x_value, NOT c.x_is_ltd) AS ht_value_ex,
         any(c.x_date) AS ht_date, any(c.x_as_of) AS ht_as_of, any(c.x_block) AS ht_block
  FROM classified AS c
  GROUP BY ht_chain, ht_token
),
shaped AS (
  SELECT *,
         count() OVER (PARTITION BY ht_chain,
                       upper(replaceRegexpAll(ht_symbol, '[^A-Za-z0-9.]', ''))) - 1
           AS ht_collisions,
         if(ifNull(ht_supply, 0) = 0, NULL,
            toFloat64(ht_tok_raw) / toFloat64(ht_supply)) AS ht_share
  FROM tok
)
