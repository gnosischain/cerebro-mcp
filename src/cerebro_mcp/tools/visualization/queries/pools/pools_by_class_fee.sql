-- Pool counts by protocol class and fee tier. Counts only: liquidity L is a
-- per-pair quantity in the pair's own units, so summing it across classes or
-- fee tiers would be arithmetic on incompatible units.
@asof_cte,
@reserves_asof_cte,
@cfg_cte,
@st_cte,
@probe_cte,
@res_cte
SELECT
  cfg.pool_class AS pool_class,
  cfg.pool_family AS pool_family,
  if(st.st_pool != '', toNullable(st.fee), NULL) AS fee,
  @fee_band_sql AS fee_band,
  count() AS pools,
  countIf(if(cfg.pool_family = 'cl', st.liquidity > 0, rs.r_any_positive)) AS live_pools,
  countIf(pr.ticks_probed) AS probed_pools
FROM cfg
LEFT JOIN st ON st.st_pool = cfg.pool_address
LEFT JOIN probe AS pr ON pr.p_pool = cfg.pool_address
LEFT JOIN res AS rs ON rs.r_pool = cfg.pool_address
GROUP BY pool_class, pool_family, fee, fee_band
ORDER BY pools DESC, pool_class, fee
