-- One row per indexer job: how far back it reaches, how many days it published,
-- and how much of its configured universe landed on the newest day. The
-- below-threshold count is CL-only and NULL for the reserves job, which has no
-- such concept — a zero there would read as "everything was probed".
@asof_cte,
@cfg_cte,
latest AS (
  SELECT job_name AS l_job, max(snapshot_date) AS l_date
  FROM @pub
  WHERE job_name IN ('@cl_job', '@reserves_job') AND target_kind = 'pool'
    AND chain_id = @chain
  GROUP BY l_job
),
cfg_counts AS (
  SELECT countIf(pool_family = 'cl') AS cfg_cl, count() AS cfg_all FROM cfg
)
SELECT
  p.job_name AS job_name,
  toString(min(p.snapshot_date)) AS first_snapshot_date,
  toString(max(p.snapshot_date)) AS last_snapshot_date,
  uniqExact(p.snapshot_date) AS days_published,
  if(p.job_name = '@cl_job', (SELECT cfg_cl FROM cfg_counts),
     (SELECT cfg_all FROM cfg_counts)) AS pools_configured,
  uniqExactIf(p.target_address, p.snapshot_date IN (SELECT l_date FROM latest))
    AS pools_published_latest,
  if(p.job_name = '@cl_job',
     toNullable(uniqExactIf(p.target_address,
       p.snapshot_date IN (SELECT l_date FROM latest)
       AND has(p.checks_passed, '@check'))), NULL) AS pools_below_threshold_latest,
  count() AS publications_total
FROM @pub AS p
WHERE p.job_name IN ('@cl_job', '@reserves_job') AND p.target_kind = 'pool'
  AND p.chain_id = @chain
GROUP BY job_name
ORDER BY job_name
