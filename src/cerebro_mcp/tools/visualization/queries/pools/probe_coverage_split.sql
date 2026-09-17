-- Why most pools have no tick-level profile. Splits the CL universe by whether
-- the indexer probed its ticks and whether it holds any liquidity, with the
-- liquidity quantiles that show the threshold is an activity cut rather than an
-- arbitrary one. Quantiles over toFloat64 of a UInt256: precision is lost far
-- beyond any displayed digit, and the alternative is no distribution at all.
@asof_cte,
@cfg_cte,
@st_cte,
@probe_cte
SELECT
  pr.ticks_probed AS ticks_probed,
  st.liquidity > 0 AS is_live,
  count() AS pools,
  quantile(0.5)(toFloat64(st.liquidity)) AS median_liquidity_float,
  quantile(0.9)(toFloat64(st.liquidity)) AS p90_liquidity_float
FROM cfg
INNER JOIN st ON st.st_pool = cfg.pool_address
LEFT JOIN probe AS pr ON pr.p_pool = cfg.pool_address
GROUP BY ticks_probed, is_live
ORDER BY ticks_probed DESC, is_live DESC
