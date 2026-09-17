-- One token across every pool that holds it. total_reserve is a sum of the SAME
-- token's balances, so it is a legitimate sum — unlike liquidity L, which is
-- never summed across pairs anywhere in this app.
@asof_cte,
@reserves_asof_cte,
@cfg_cte,
@st_cte,
@probe_cte,
@res_cte,
holders AS (
  SELECT cfg.pool_address AS h_pool, cfg.pool_family AS h_family
  FROM cfg
  WHERE has(cfg.assets, {token:String})
),
tok AS (
  SELECT any(m.symbol) AS symbol, any(m.name) AS token_name,
         any(m.decimals) AS decimals, any(m.resolution_status) AS resolution_status
  FROM @db.@meta_view AS m
  WHERE m.chain_id = @chain AND m.token_address = {token:String}
)
SELECT
  {token:String} AS token_address,
  concat('token ', substring({token:String}, 1, 10), '...',
         substring({token:String}, 39, 4)) AS entity_label,
  m.symbol AS symbol,
  m.token_name AS token_name,
  m.decimals AS decimals,
  if(m.resolution_status = '', 'unresolved', m.resolution_status) AS resolution_status,
  m.decimals IS NOT NULL AS is_resolved,
  count() AS pools_count,
  countIf(h.h_family = 'cl') AS cl_pools,
  countIf(h.h_family = 'reserves_only') AS reserves_only_pools,
  countIf(st.liquidity > 0) AS live_pools,
  countIf(pr.ticks_probed) AS probed_pools,
  toString((SELECT as_of FROM asof)) AS as_of,
  toString((SELECT max(ras_of) FROM rasof)) AS reserves_as_of,
  toString(sum(toUInt256OrZero(if(has(rs.r_tokens, {token:String}),
    rs.r_balances[indexOf(rs.r_tokens, {token:String})], '0')))) AS total_reserve_raw,
  if(m.decimals IS NULL, NULL,
     sum(toFloat64(toUInt256OrZero(if(has(rs.r_tokens, {token:String}),
       rs.r_balances[indexOf(rs.r_tokens, {token:String})], '0'))))
     / pow(10, toInt16(m.decimals))) AS total_reserve_units
FROM holders AS h
CROSS JOIN tok AS m
LEFT JOIN st ON st.st_pool = h.h_pool
LEFT JOIN probe AS pr ON pr.p_pool = h.h_pool
LEFT JOIN res AS rs ON rs.r_pool = h.h_pool
GROUP BY symbol, token_name, decimals, resolution_status, is_resolved
ORDER BY token_address
