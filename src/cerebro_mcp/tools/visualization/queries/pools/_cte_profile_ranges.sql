-- The liquidity profile, recomputed from initialized ticks rather than read
-- from the derived v_pool_liquidity_profile view.
--
-- Why recompute: the derived view only starts 2025-09-01, while the tick view
-- goes back to 2023-10. The window form below was checked against the view on a
-- day both cover and reproduced every range exactly, so recomputing costs
-- nothing in fidelity and buys two extra years of history plus one contract for
-- every date.
--
-- The running sum of liquidity_net ordered by tick IS the active liquidity
-- between that tick and the next one, which is the definition of a
-- concentrated-liquidity range. leadInFrame supplies the next tick; its
-- Nullable default form is rejected by ClickHouse (code 36), so the last tick
-- of each pool-day is dropped by comparing against the partition max instead —
-- that tick closes the final range and opens nothing.
ticks AS (
  SELECT t.pool_address AS r_pool,
         t.snapshot_date AS r_date,
         t.tick AS tick,
         t.liquidity_net AS net
  FROM @db.@view AS t
  WHERE t.chain_id = @chain AND t.job_name = '@job'
    AND @pool_sql AND @date_sql
),
ranges AS (
  SELECT r_pool, r_date, tick_lower, tick_upper, active
  FROM (
    SELECT r_pool,
           r_date,
           tick AS tick_lower,
           leadInFrame(tick) OVER w AS tick_upper,
           sum(net) OVER (PARTITION BY r_pool, r_date ORDER BY tick
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS active,
           max(tick) OVER (PARTITION BY r_pool, r_date) AS max_tick
    FROM ticks
    WINDOW w AS (PARTITION BY r_pool, r_date ORDER BY tick
                 ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING)
  )
  WHERE tick_lower < max_tick
)
