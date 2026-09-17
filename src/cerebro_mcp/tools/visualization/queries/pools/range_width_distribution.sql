-- How wide LP positions are, in ticks. Ranges carrying no active liquidity are
-- gaps between positions rather than positions, so they are excluded and the
-- count of what was excluded is returned as its own bucket rather than left
-- unstated.
--
-- The share denominator is a window total over the same aggregation, NOT a
-- second CTE reference: ClickHouse inlines a CTE per reference, and reading
-- widths twice would re-run the whole tick scan behind it.
@asof_cte,
@ranges_cte,
widths AS (
  SELECT r.r_pool AS pool,
         r.active > 0 AS has_liquidity,
         multiIf(r.tick_lower <= -@full_tick AND r.tick_upper >= @full_tick, 6,
                 r.tick_upper - r.tick_lower <= 10, 0,
                 r.tick_upper - r.tick_lower <= 100, 1,
                 r.tick_upper - r.tick_lower <= 1000, 2,
                 r.tick_upper - r.tick_lower <= 10000, 3,
                 r.tick_upper - r.tick_lower <= 100000, 4,
                 5) AS bucket_order
  FROM ranges AS r
)
SELECT
  bucket_order,
  multiIf(bucket_order = 0, 'up to 10 ticks',
          bucket_order = 1, 'up to 100 ticks',
          bucket_order = 2, 'up to 1k ticks',
          bucket_order = 3, 'up to 10k ticks',
          bucket_order = 4, 'up to 100k ticks',
          bucket_order = 5, 'wider than 100k ticks',
          'full range') AS width_bucket,
  count() AS ranges,
  uniqExact(pool) AS pools,
  count() / nullIf(sum(count()) OVER (), 0) AS share_of_ranges
FROM widths
WHERE has_liquidity
GROUP BY bucket_order
ORDER BY bucket_order
