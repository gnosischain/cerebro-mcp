-- Fees accrued to in-range liquidity, ESTIMATED from the day-over-day change in
-- the pool's global fee-growth accumulators times the liquidity in range:
--   fees = (fee_growth_t - fee_growth_t-1) * liquidity / 2^128
-- It is an estimate because liquidity is sampled once a day while it changes
-- continuously, and because the accumulator advances only while the price is in
-- range. The UInt256 subtraction happens BEFORE the float cast, so the delta
-- keeps full precision even though the accumulators are enormous.
--
-- Every reference below is qualified with base. on purpose: the projection
-- aliases toString(snapshot_date) back to `snapshot_date`, and an output alias
-- SHADOWS the source column of the same name, so a bare `snapshot_date -
-- prev_date` resolves to the String and raises ILLEGAL_TYPE_OF_ARGUMENT.
-- Lesson: ch-output-alias-shadows-column.
--
-- NULL, never 0, on the first row of a series, on a gap where the accumulator
-- went backwards (a pool redeployed at the same address), and where liquidity
-- was zero: none of those are measurements of no fees.
@asof_cte,
@cfg_cte,
@meta_cte,
dec AS (SELECT @asset_decimals_sql AS d FROM cfg CROSS JOIN mm),
@probe_days_cte,
base AS (
  SELECT s.snapshot_date AS snapshot_date,
         s.liquidity AS liquidity,
         s.fee_growth_global_0_x128 AS fg0,
         s.fee_growth_global_1_x128 AS fg1,
         lagInFrame(s.fee_growth_global_0_x128) OVER w AS prev0,
         lagInFrame(s.fee_growth_global_1_x128) OVER w AS prev1,
         lagInFrame(s.snapshot_date) OVER w AS prev_date,
         row_number() OVER w AS rn
  FROM @db.@view AS s
  WHERE s.chain_id = @chain AND s.job_name = '@job'
    AND s.pool_address = {pool:String} AND @window_state
  WINDOW w AS (ORDER BY s.snapshot_date ROWS BETWEEN 1 PRECEDING AND CURRENT ROW)
)
SELECT
  toString(base.snapshot_date) AS snapshot_date,
  if(rn = 1, NULL, toNullable(toString(base.prev_date))) AS prev_snapshot_date,
  if(rn = 1, NULL, toNullable(toUInt32(base.snapshot_date - base.prev_date)))
    AS gap_days,
  toFloat64(liquidity) AS liquidity_float,
  toString(fg0) AS fee_growth_global_0_raw,
  toString(fg1) AS fee_growth_global_1_raw,
  if(rn = 1 OR fg0 < prev0, NULL, toNullable(toString(fg0 - prev0))) AS delta_fg0_raw,
  if(rn = 1 OR fg1 < prev1, NULL, toNullable(toString(fg1 - prev1))) AS delta_fg1_raw,
  if(rn = 1 OR fg0 < prev0 OR liquidity = 0, NULL,
     toNullable(toFloat64(fg0 - prev0) * toFloat64(liquidity) / pow(2, 128)))
    AS fees0_raw_est,
  if(rn = 1 OR fg1 < prev1 OR liquidity = 0, NULL,
     toNullable(toFloat64(fg1 - prev1) * toFloat64(liquidity) / pow(2, 128)))
    AS fees1_raw_est,
  if(fees0_raw_est IS NULL OR arrayElement(dec.d, 1) < 0, NULL,
     fees0_raw_est / pow(10, arrayElement(dec.d, 1))) AS fees0_units_est,
  if(fees1_raw_est IS NULL OR arrayElement(dec.d, 2) < 0, NULL,
     fees1_raw_est / pow(10, arrayElement(dec.d, 2))) AS fees1_units_est,
  pd.ticks_probed AS ticks_probed
FROM base
CROSS JOIN dec
LEFT JOIN probe_days AS pd ON pd.pd_date = base.snapshot_date
ORDER BY base.snapshot_date
