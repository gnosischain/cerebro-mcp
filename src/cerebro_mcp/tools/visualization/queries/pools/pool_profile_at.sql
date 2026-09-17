-- The liquidity profile of one pool on one day: active liquidity between each
-- pair of initialized ticks, with the price bounds each range covers.
--
-- matches_state_liquidity is a live reconciliation, not decoration: the range
-- containing the current tick must carry exactly the pool's reported liquidity,
-- and a mismatch means the recompute and the pool disagree. is_gap marks a
-- range with no active liquidity — a hole between positions, which the chart
-- draws as empty rather than omitting.
@asof_cte,
@cfg_cte,
@meta_cte,
@st_cte,
@ranges_cte
SELECT
  r.r_pool AS pool_address,
  toString(r.r_date) AS as_of,
  r.tick_lower AS tick_lower,
  r.tick_upper AS tick_upper,
  r.tick_upper - r.tick_lower AS width_ticks,
  pow(1.0001, r.tick_lower) AS price_lower_raw,
  pow(1.0001, r.tick_upper) AS price_upper_raw,
  if(arrayElement(dec, 1) < 0 OR arrayElement(dec, 2) < 0, NULL,
     pow(1.0001, r.tick_lower)
     * pow(10, toInt16(arrayElement(dec, 1)) - toInt16(arrayElement(dec, 2))))
    AS price_lower_adjusted,
  if(arrayElement(dec, 1) < 0 OR arrayElement(dec, 2) < 0, NULL,
     pow(1.0001, r.tick_upper)
     * pow(10, toInt16(arrayElement(dec, 1)) - toInt16(arrayElement(dec, 2))))
    AS price_upper_adjusted,
  toString(r.active) AS active_liquidity_raw,
  toFloat64(r.active) AS active_liquidity_float,
  r.active = 0 AS is_gap,
  s.current_tick >= r.tick_lower AND s.current_tick < r.tick_upper
    AS contains_current_tick,
  r.tick_lower <= -@full_tick AND r.tick_upper >= @full_tick AS is_full_range,
  s.current_tick AS current_tick,
  multiIf(s.current_tick < r.tick_lower, r.tick_lower - s.current_tick,
          s.current_tick >= r.tick_upper, r.tick_upper - s.current_tick, 0)
    AS distance_ticks,
  if(contains_current_tick, toNullable(r.active = toInt256(s.liquidity)), NULL)
    AS matches_state_liquidity,
  'tick_window_recompute' AS profile_source
FROM ranges AS r
INNER JOIN st AS s ON s.st_pool = r.r_pool
CROSS JOIN (
  SELECT @asset_decimals_sql AS dec FROM cfg CROSS JOIN mm
) AS d
ORDER BY tick_lower
