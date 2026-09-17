-- The pool table. Base is the configured universe, so a pool the indexer knows
-- about but did not publish today still appears with NULL state rather than
-- vanishing. Every concentrated-liquidity column is explicitly NULL for a
-- reserves-only pool: ClickHouse fills an unmatched LEFT JOIN with zeros, and a
-- zero current_tick reads as "price 1", which is a fabricated number.
@asof_cte,
@reserves_asof_cte,
@cfg_cte,
@meta_cte,
@st_cte,
@probe_cte,
@res_cte,
@life_cte
SELECT
  cfg.pool_address AS pool_address,
  cfg.pool_name AS pool_name,
  cfg.pool_class AS pool_class,
  cfg.pool_family AS pool_family,
  cfg.n_assets AS n_assets,
  cfg.assets AS assets,
  @asset_symbols_sql AS asset_symbols,
  @asset_decimals_sql AS asset_decimals,
  cfg.token0 AS token0,
  nullIf(arrayElement(asset_symbols, 1), '') AS token0_symbol,
  if(arrayElement(asset_decimals, 1) < 0, NULL,
     toUInt8(arrayElement(asset_decimals, 1))) AS token0_decimals,
  arrayElement(asset_decimals, 1) >= 0 AS token0_resolved,
  if(arrayElement(asset_symbols, 1) != '', arrayElement(asset_symbols, 1),
     concat(substring(cfg.token0, 1, 6), '...', substring(cfg.token0, 39, 4)))
    AS token0_label,
  cfg.token1 AS token1,
  nullIf(arrayElement(asset_symbols, 2), '') AS token1_symbol,
  if(arrayElement(asset_decimals, 2) < 0, NULL,
     toUInt8(arrayElement(asset_decimals, 2))) AS token1_decimals,
  arrayElement(asset_decimals, 2) >= 0 AS token1_resolved,
  if(arrayElement(asset_symbols, 2) != '', arrayElement(asset_symbols, 2),
     concat(substring(cfg.token1, 1, 6), '...', substring(cfg.token1, 39, 4)))
    AS token1_label,
  toString((SELECT as_of FROM asof)) AS as_of,
  st.st_pool != '' AS has_state,
  if(has_state, toNullable(st.current_tick), NULL) AS current_tick,
  if(has_state, toNullable(@price_raw_sql), NULL) AS price_raw,
  @price_adjusted_sql AS price_adjusted,
  if(has_state, toNullable(toString(st.liquidity)), NULL) AS liquidity_raw,
  if(has_state, toNullable(toFloat64(st.liquidity)), NULL) AS liquidity_float,
  if(cfg.pool_family = 'cl', st.liquidity > 0, rs.r_any_positive) AS is_live,
  if(has_state, toNullable(st.tick_count), NULL) AS tick_count,
  if(has_state, toNullable(st.tick_spacing), NULL) AS tick_spacing,
  if(has_state, toNullable(st.fee), NULL) AS fee,
  @fee_band_sql AS fee_band,
  pr.ticks_probed AS ticks_probed,
  if(rs.r_pool != '', toNullable(toString(rs.r_date)), NULL) AS reserves_as_of,
  if(has(rs.r_tokens, cfg.token0),
     toNullable(rs.r_balances[indexOf(rs.r_tokens, cfg.token0)]), NULL) AS reserve0_raw,
  if(has(rs.r_tokens, cfg.token1),
     toNullable(rs.r_balances[indexOf(rs.r_tokens, cfg.token1)]), NULL) AS reserve1_raw,
  if(reserve0_raw IS NULL OR arrayElement(asset_decimals, 1) < 0, NULL,
     toFloat64(toUInt256OrZero(reserve0_raw))
     / pow(10, arrayElement(asset_decimals, 1))) AS reserve0_units,
  if(reserve1_raw IS NULL OR arrayElement(asset_decimals, 2) < 0, NULL,
     toFloat64(toUInt256OrZero(reserve1_raw))
     / pow(10, arrayElement(asset_decimals, 2))) AS reserve1_units,
  toString(lf.first_published) AS first_published,
  toString(lf.last_published) AS last_published,
  lf.days_published AS days_published,
  cfg.deployment_block AS deployment_block,
  if(has_state, toNullable(st.st_anchor_block), NULL) AS anchor_block
FROM cfg
CROSS JOIN mm
LEFT JOIN st ON st.st_pool = cfg.pool_address
LEFT JOIN probe AS pr ON pr.p_pool = cfg.pool_address
LEFT JOIN res AS rs ON rs.r_pool = cfg.pool_address
LEFT JOIN life AS lf ON lf.l_pool = cfg.pool_address
WHERE @class_sql AND @family_sql AND @fee_sql AND @token_sql
  AND @live_sql AND @probed_sql AND @query_sql
ORDER BY @sort_fragment
