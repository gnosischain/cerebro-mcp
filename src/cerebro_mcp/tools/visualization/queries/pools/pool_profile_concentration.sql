-- Where this pool's liquidity sits relative to its own current price. Shares
-- are tick-weighted (active liquidity times ticks covered), so they are
-- proportional to the virtual reserves each band supplies. A full-range
-- position covers every band, which is why its share is reported as its own
-- row instead of being netted out of the others.
@asof_cte,
@st_cte,
@ranges_cte,
per_band AS (
  SELECT
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
    sum(toFloat64(r.active) * (r.tick_upper - r.tick_lower)
        * (r.tick_lower <= -@full_tick AND r.tick_upper >= @full_tick)) AS wfull,
    countIf(abs(r.tick_upper - s.current_tick) <= @band1
            OR abs(r.tick_lower - s.current_tick) <= @band1) AS n1,
    countIf(abs(r.tick_upper - s.current_tick) <= @band5
            OR abs(r.tick_lower - s.current_tick) <= @band5) AS n5,
    countIf(abs(r.tick_upper - s.current_tick) <= @band10
            OR abs(r.tick_lower - s.current_tick) <= @band10) AS n10,
    countIf(r.tick_lower <= -@full_tick AND r.tick_upper >= @full_tick) AS nfull,
    sumIf(toFloat64(r.active),
          s.current_tick >= r.tick_lower AND s.current_tick < r.tick_upper)
      AS liquidity_at_current_tick_float
  FROM ranges AS r
  INNER JOIN st AS s ON s.st_pool = r.r_pool
  WHERE r.active > 0
)
SELECT band, band_ticks, tick_weighted_share, ranges_in_band,
       liquidity_at_current_tick_float
FROM (
  SELECT ['1pct', '5pct', '10pct', 'full_range'] AS bands,
         [toInt32(@band1), toInt32(@band5), toInt32(@band10), toInt32(@full_tick)]
           AS band_tick_list,
         [w1 / nullIf(total_w, 0), w5 / nullIf(total_w, 0),
          w10 / nullIf(total_w, 0), wfull / nullIf(total_w, 0)] AS shares,
         [n1, n5, n10, nfull] AS counts,
         liquidity_at_current_tick_float
  FROM per_band
)
ARRAY JOIN bands AS band, band_tick_list AS band_ticks, shares AS tick_weighted_share,
           counts AS ranges_in_band
ORDER BY band_ticks
