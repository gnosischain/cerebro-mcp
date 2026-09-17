-- Every pool holding one token, ranked by how much of that token it holds.
-- reserve_share is a share of the token's own observed reserves across these
-- pools — one unit throughout, so the ratio means something. It is NOT a share
-- of supply and NOT a value ranking: this plane carries no prices.
--
-- price_of_token_in_counter is oriented so the SUBJECT token is the base,
-- which is the only orientation that means the same thing across a list of
-- pools pairing it with different counters. It is the decimals-adjusted price,
-- so it is NULL wherever either side's decimals were never observed — the raw
-- orientation is still available from price_raw plus token_is_token0.
--
-- The denominator is a window total over this same pass, NOT a second CTE
-- reference: ClickHouse inlines a CTE per reference, and holders carries the
-- per-asset label resolution, so reading it twice ran that whole map twice.
@asof_cte,
@reserves_asof_cte,
@cfg_cte,
@meta_cte,
@st_cte,
@probe_cte,
@res_cte,
holders AS (
  SELECT cfg.pool_address AS h_pool, cfg.pool_name AS h_name,
         cfg.pool_class AS h_class, cfg.pool_family AS h_family,
         cfg.assets AS h_assets, cfg.token0 AS h_token0,
         @asset_symbols_sql AS h_symbols,
         @asset_decimals_sql AS h_decimals
  FROM cfg CROSS JOIN mm
  WHERE has(cfg.assets, {token:String})
)
SELECT
  h.h_pool AS pool_address,
  h.h_name AS pool_name,
  h.h_class AS pool_class,
  h.h_family AS pool_family,
  st.st_pool != '' AS has_state,
  if(has_state, toNullable(st.fee), NULL) AS fee,
  @fee_band_sql AS fee_band,
  h.h_token0 = {token:String} AS token_is_token0,
  arrayFilter(a -> a != {token:String}, h.h_assets) AS counter_tokens,
  arrayMap(i -> if(arrayElement(h.h_symbols, i) != '', arrayElement(h.h_symbols, i),
                   concat(substring(arrayElement(h.h_assets, i), 1, 6), '...',
                          substring(arrayElement(h.h_assets, i), 39, 4))),
           arrayFilter(i -> arrayElement(h.h_assets, i) != {token:String},
                       arrayEnumerate(h.h_assets))) AS counter_labels,
  if(has_state, toNullable(st.current_tick), NULL) AS current_tick,
  if(has_state, toNullable(@price_raw_sql), NULL) AS price_raw,
  if(NOT has_state OR arrayElement(h.h_decimals, 1) < 0
     OR arrayElement(h.h_decimals, 2) < 0, NULL,
     @price_raw_sql * pow(10, toInt16(arrayElement(h.h_decimals, 1))
                              - toInt16(arrayElement(h.h_decimals, 2))))
    AS price_adjusted,
  if(price_adjusted IS NULL, NULL,
     if(token_is_token0, price_adjusted, 1 / nullIf(price_adjusted, 0)))
    AS price_of_token_in_counter,
  if(has_state, toNullable(toString(st.liquidity)), NULL) AS liquidity_raw,
  if(has_state, toNullable(toFloat64(st.liquidity)), NULL) AS liquidity_float,
  if(h.h_family = 'cl', st.liquidity > 0, rs.r_any_positive) AS is_live,
  pr.ticks_probed AS ticks_probed,
  if(has(rs.r_tokens, {token:String}),
     toNullable(rs.r_balances[indexOf(rs.r_tokens, {token:String})]), NULL)
    AS reserve_token_raw,
  if(arrayElement(h.h_decimals, indexOf(h.h_assets, {token:String})) < 0, NULL,
     toUInt8(arrayElement(h.h_decimals, indexOf(h.h_assets, {token:String}))))
    AS token_decimals,
  if(reserve_token_raw IS NULL OR token_decimals IS NULL, NULL,
     toFloat64(toUInt256OrZero(reserve_token_raw)) / pow(10, token_decimals))
    AS reserve_token_units,
  toFloat64(toUInt256OrZero(if(has(rs.r_tokens, {token:String}),
    rs.r_balances[indexOf(rs.r_tokens, {token:String})], '0')))
    / nullIf(sum(toFloat64(toUInt256OrZero(if(has(rs.r_tokens, {token:String}),
        rs.r_balances[indexOf(rs.r_tokens, {token:String})], '0')))) OVER (), 0)
    AS reserve_share
FROM holders AS h
LEFT JOIN st ON st.st_pool = h.h_pool
LEFT JOIN probe AS pr ON pr.p_pool = h.h_pool
LEFT JOIN res AS rs ON rs.r_pool = h.h_pool
ORDER BY reserve_share DESC NULLS LAST, liquidity_float DESC NULLS LAST, pool_address
