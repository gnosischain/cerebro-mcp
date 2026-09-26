-- Concentrated-liquidity pool state at the resolved as-of. Reads the published
-- view, never the raw pool_cl_state table: the raw table's sort key includes
-- attempt_id, so even FINAL leaves one row per attempt and a pool that was
-- retried appears twice. The view INNER JOINs census_publications and is unique
-- per (pool, date) — and must NOT be FINAL'd.
st AS (
  SELECT s.pool_address AS st_pool,
         s.snapshot_date AS st_date,
         s.sqrt_price_x96 AS sqrt_price_x96,
         s.current_tick AS current_tick,
         s.liquidity AS liquidity,
         s.tick_spacing AS tick_spacing,
         s.fee AS fee,
         s.tick_count AS tick_count,
         s.anchor_block AS st_anchor_block
  FROM @db.@view AS s
  WHERE s.chain_id = @chain AND s.job_name = '@job'
    AND s.snapshot_date = (SELECT as_of FROM asof)
    AND @pool_sql
)
