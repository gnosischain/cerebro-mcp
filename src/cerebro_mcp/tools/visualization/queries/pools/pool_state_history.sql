-- One pool's state over time. Bounded by the window predicate rather than by a
-- row cap so the series is always a contiguous stretch ending at the as-of,
-- never an arbitrary sample.
@asof_cte,
@cfg_cte,
@meta_cte,
dec AS (SELECT @asset_decimals_sql AS d FROM cfg CROSS JOIN mm),
@probe_days_cte
SELECT
  toString(s.snapshot_date) AS snapshot_date,
  s.anchor_block AS anchor_block,
  s.current_tick AS current_tick,
  @price_raw_sql AS price_raw,
  if(arrayElement(dec.d, 1) < 0 OR arrayElement(dec.d, 2) < 0, NULL,
     @price_raw_sql * pow(10, toInt16(arrayElement(dec.d, 1))
                              - toInt16(arrayElement(dec.d, 2)))) AS price_adjusted,
  toString(s.liquidity) AS liquidity_raw,
  toFloat64(s.liquidity) AS liquidity_float,
  s.liquidity > 0 AS is_live,
  s.tick_count AS tick_count,
  s.fee AS fee,
  s.tick_spacing AS tick_spacing,
  toString(s.fee_growth_global_0_x128) AS fee_growth_global_0_raw,
  toString(s.fee_growth_global_1_x128) AS fee_growth_global_1_raw,
  pd.ticks_probed AS ticks_probed
FROM @db.@view AS s
CROSS JOIN dec
LEFT JOIN probe_days AS pd ON pd.pd_date = s.snapshot_date
WHERE s.chain_id = @chain AND s.job_name = '@job'
  AND s.pool_address = {pool:String} AND @window_state
ORDER BY snapshot_date
