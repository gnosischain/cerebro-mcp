-- Universe growth over time. The publication counts come from the cheap
-- publications table; only the live count needs the state view, so it is the
-- one windowed scan here and it is bounded by the window predicate.
@asof_cte,
pubs AS (
  SELECT p.snapshot_date AS bucket,
         uniqExactIf(p.target_address, p.job_name = '@cl_job') AS pools_published_cl,
         uniqExactIf(p.target_address, p.job_name = '@cl_job'
                     AND NOT has(p.checks_passed, '@check')) AS pools_probed,
         uniqExactIf(p.target_address, p.job_name = '@reserves_job')
           AS pools_published_reserves
  FROM @pub AS p
  WHERE p.target_kind = 'pool' AND p.chain_id = @chain AND @window_pub
  GROUP BY bucket
),
live AS (
  SELECT s.snapshot_date AS l_bucket, countIf(s.liquidity > 0) AS pools_live_cl
  FROM @db.@view AS s
  WHERE s.chain_id = @chain AND s.job_name = '@cl_job' AND @window_state
  GROUP BY l_bucket
)
SELECT
  toString(pubs.bucket) AS bucket,
  pubs.pools_published_cl AS pools_published_cl,
  live.pools_live_cl AS pools_live_cl,
  pubs.pools_probed AS pools_probed,
  pubs.pools_published_reserves AS pools_published_reserves
FROM pubs
LEFT JOIN live ON live.l_bucket = pubs.bucket
ORDER BY bucket
