-- How tightly liquidity sits around the current price, across every probed
-- pool. The share is TICK-WEIGHTED: each range contributes its active
-- liquidity times the number of ticks it covers, which is proportional to the
-- virtual reserves it supplies. That makes a full-range position dominate the
-- denominator by construction, which is why the full-range share is reported
-- beside the band shares rather than folded into them.
--
-- Pools with no probed ranges contribute nothing and are simply absent from
-- pools_measured; the overview states how many those are.
@asof_cte,
@st_cte,
@ranges_cte,
per_pool AS (
  SELECT r.r_pool AS pool,
         sum(toFloat64(r.active) * (r.tick_upper - r.tick_lower)) AS total_w,
         sum(toFloat64(r.active) * greatest(0,
             least(r.tick_upper, s.current_tick + @band1)
             - greatest(r.tick_lower, s.current_tick - @band1))) AS w1,
         sum(toFloat64(r.active) * greatest(0,
             least(r.tick_upper, s.current_tick + @band5)
             - greatest(r.tick_lower, s.current_tick - @band5))) AS w5,
         sum(toFloat64(r.active) * greatest(0,
             least(r.tick_upper, s.current_tick + @band10)
             - greatest(r.tick_lower, s.current_tick - @band10))) AS w10,
         countIf(r.tick_lower <= -@full_tick AND r.tick_upper >= @full_tick)
           AS full_ranges,
         count() AS ranges
  FROM ranges AS r
  INNER JOIN st AS s ON s.st_pool = r.r_pool
  WHERE r.active > 0
  GROUP BY pool
),
agg AS (
  SELECT
    ['share_1pct', 'share_5pct', 'share_10pct', 'ranges_per_pool', 'has_full_range'] AS metrics,
    count() AS pools_measured,
    [quantile(0.25)(w1 / nullIf(total_w, 0)),
     quantile(0.25)(w5 / nullIf(total_w, 0)),
     quantile(0.25)(w10 / nullIf(total_w, 0)),
     toNullable(quantile(0.25)(ranges)),
     CAST(NULL AS Nullable(Float64))] AS q25s,
    [quantile(0.5)(w1 / nullIf(total_w, 0)),
     quantile(0.5)(w5 / nullIf(total_w, 0)),
     quantile(0.5)(w10 / nullIf(total_w, 0)),
     toNullable(quantile(0.5)(ranges)),
     CAST(NULL AS Nullable(Float64))] AS medians,
    [quantile(0.75)(w1 / nullIf(total_w, 0)),
     quantile(0.75)(w5 / nullIf(total_w, 0)),
     quantile(0.75)(w10 / nullIf(total_w, 0)),
     toNullable(quantile(0.75)(ranges)),
     CAST(NULL AS Nullable(Float64))] AS q75s,
    [CAST(NULL AS Nullable(UInt64)), CAST(NULL AS Nullable(UInt64)),
     CAST(NULL AS Nullable(UInt64)), CAST(NULL AS Nullable(UInt64)),
     toNullable(countIf(full_ranges > 0))] AS trues
  FROM per_pool
)
SELECT metric, pools_measured, q25, median, q75, pools_true
FROM agg
ARRAY JOIN metrics AS metric, q25s AS q25, medians AS median, q75s AS q75,
           trues AS pools_true
ORDER BY metric
