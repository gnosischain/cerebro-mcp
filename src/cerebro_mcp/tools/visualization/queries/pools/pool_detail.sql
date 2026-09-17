-- One pool, everything the header needs. entity_label is composed server-side
-- from the protocol class and the address and NEVER from a token symbol:
-- symbols on this chain are attacker-authored and the breadcrumb renders its
-- label raw.
@asof_cte,
@reserves_asof_cte,
@cfg_cte,
@meta_cte,
@st_cte,
@probe_cte,
@res_cte,
@life_cte,
hist AS (
  SELECT countIf(h.liquidity > 0) AS days_live,
         min(h.snapshot_date) AS state_from
  FROM @db.@view AS h
  WHERE h.chain_id = @chain AND h.job_name = '@cl_job'
    AND h.pool_address = {pool:String}
),
probed_from AS (
  -- minOrNull, not min: a pool the indexer never probed has NO rows here, and a
  -- bare min() over an empty set returns the Date default 1970-01-01 — which the
  -- UI then printed as "profile since 1970-01-01" for a pool that has no profile
  -- at any date. Nothing measured means NULL.
  SELECT minOrNull(p.snapshot_date) AS profile_available_from
  FROM @pub AS p
  WHERE p.job_name = '@cl_job' AND p.target_kind = 'pool' AND p.chain_id = @chain
    AND p.target_address = {pool:String} AND NOT has(p.checks_passed, '@check')
)
SELECT
  cfg.pool_address AS pool_address,
  concat(cfg.pool_class, ' ', substring(cfg.pool_address, 1, 10), '...',
         substring(cfg.pool_address, 39, 4)) AS entity_label,
  cfg.pool_name AS pool_name,
  cfg.pool_class AS pool_class,
  cfg.pool_family AS pool_family,
  nullIf(cfg.pool_id, '') AS pool_id,
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
  rs.r_tokens AS reserve_tokens,
  rs.r_balances AS reserve_raw,
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
  (SELECT days_live FROM hist) AS days_live,
  toString((SELECT profile_available_from FROM probed_from)) AS profile_available_from,
  cfg.deployment_block AS deployment_block,
  if(has_state, toNullable(st.st_anchor_block), NULL) AS anchor_block
FROM cfg
CROSS JOIN mm
LEFT JOIN st ON st.st_pool = cfg.pool_address
LEFT JOIN probe AS pr ON pr.p_pool = cfg.pool_address
LEFT JOIN res AS rs ON rs.r_pool = cfg.pool_address
LEFT JOIN life AS lf ON lf.l_pool = cfg.pool_address
ORDER BY pool_address
