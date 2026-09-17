-- The profile over time: a tick-by-date grid of active liquidity.
--
-- Two independent bounds keep it inside the row budget whatever the window.
-- Dates: only days the pool was actually probed are candidates, and they are
-- sampled by a stride so at most the configured number reach the grid. Ticks:
-- the axis spans the pool's own current-tick excursion padded by a fixed
-- percentage of price, divided into at most the configured number of buckets,
-- never finer than the pool's tick spacing.
--
-- A range is spread across the buckets it covers in proportion to the ticks it
-- covers in each, so a full-range position contributes its liquidity evenly
-- across the visible axis instead of being clipped away or drawn as one wall.
@asof_cte,
days_all AS (
  SELECT p.snapshot_date AS d_date
  FROM @pub AS p
  WHERE p.job_name = '@job' AND p.target_kind = 'pool' AND p.chain_id = @chain
    AND p.target_address = {pool:String}
    AND NOT has(p.checks_passed, '@check')
    AND p.snapshot_date <= (SELECT as_of FROM asof)
    AND @window_pub
),
grid AS (
  SELECT count() AS dates_total,
         min(d_date) AS d0,
         greatest(1, toUInt32(ceil(count() / @max_dates))) AS date_step
  FROM days_all
),
days AS (
  SELECT d_date AS snapshot_date
  FROM days_all
  CROSS JOIN grid
  WHERE (d_date - grid.d0) % grid.date_step = 0
),
stday AS (
  SELECT s.snapshot_date AS st_date, s.current_tick AS current_tick,
         s.tick_spacing AS tick_spacing
  FROM @db.@view AS s
  WHERE s.chain_id = @chain AND s.job_name = '@job'
    AND s.pool_address = {pool:String}
    AND s.snapshot_date IN (SELECT snapshot_date FROM days)
),
axis AS (
  SELECT min(current_tick) - @axis_pad AS axis_lo,
         max(current_tick) + @axis_pad AS axis_hi,
         greatest(max(tick_spacing), toInt32(ceil(
           (max(current_tick) + @axis_pad - min(current_tick) + @axis_pad)
           / @tick_buckets))) AS tick_step,
         (SELECT dates_total FROM grid) AS dates_total,
         (SELECT date_step FROM grid) AS date_step,
         (SELECT count() FROM days) AS dates_sampled
  FROM stday
),
@ranges_cte,
cells AS (
  SELECT r.r_date AS bucket_date,
         r.tick_lower AS lo,
         r.tick_upper AS hi,
         toFloat64(r.active) AS active,
         a.axis_lo AS axis_lo, a.axis_hi AS axis_hi, a.tick_step AS tick_step,
         a.dates_total AS dates_total, a.date_step AS date_step,
         a.dates_sampled AS dates_sampled,
         arrayJoin(range(
           toUInt32(intDiv(greatest(r.tick_lower, a.axis_lo) - a.axis_lo, a.tick_step)),
           toUInt32(intDiv(least(r.tick_upper, a.axis_hi) - a.axis_lo - 1, a.tick_step)) + 1
         )) AS b
  FROM ranges AS r
  CROSS JOIN axis AS a
  WHERE r.active > 0 AND r.tick_upper > a.axis_lo AND r.tick_lower < a.axis_hi
)
SELECT
  toString(cells.bucket_date) AS bucket_date,
  cells.axis_lo + toInt32(cells.b) * cells.tick_step AS tick_bucket_lo,
  cells.axis_lo + (toInt32(cells.b) + 1) * cells.tick_step AS tick_bucket_hi,
  sum(cells.active * (least(cells.hi, tick_bucket_hi)
                      - greatest(cells.lo, tick_bucket_lo)))
    / any(cells.tick_step) AS liquidity_float,
  any(sd.current_tick) AS current_tick,
  any(cells.axis_lo) AS axis_lo,
  any(cells.axis_hi) AS axis_hi,
  any(cells.tick_step) AS tick_step,
  any(cells.date_step) AS date_step_days,
  any(cells.dates_total) AS dates_total,
  any(cells.dates_sampled) AS dates_sampled
FROM cells
LEFT JOIN stday AS sd ON sd.st_date = cells.bucket_date
GROUP BY bucket_date, tick_bucket_lo, tick_bucket_hi
ORDER BY bucket_date, tick_bucket_lo
