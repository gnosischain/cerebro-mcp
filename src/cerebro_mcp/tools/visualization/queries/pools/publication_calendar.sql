-- Per-day publication counts for both jobs, with the CL job's integrity checks
-- broken out. Publications only, so this is cheap enough to run over all
-- history and is the honest answer to "was this day complete".
@asof_cte,
@cfg_cte,
cfg_counts AS (
  SELECT countIf(pool_family = 'cl') AS cfg_cl, count() AS cfg_all FROM cfg
)
SELECT
  toString(p.snapshot_date) AS snapshot_date,
  p.job_name AS job_name,
  max(p.anchor_block) AS anchor_block,
  uniqExact(p.target_address) AS pools_published,
  if(p.job_name = '@cl_job',
     toNullable(countIf(has(p.checks_passed, '@check'))), NULL)
    AS pools_below_threshold,
  if(p.job_name = '@cl_job',
     toNullable(countIf(NOT has(p.checks_passed, '@check'))), NULL) AS pools_probed,
  if(p.job_name = '@cl_job',
     toNullable(countIf(has(p.checks_passed, 'cl_liquidity_net_sum_zero'))), NULL)
    AS net_sum_zero_passed,
  if(p.job_name = '@cl_job',
     toNullable(countIf(has(p.checks_passed, 'cl_active_liquidity_reconciles'))), NULL)
    AS reconciles_passed,
  if(p.job_name = '@cl_job', (SELECT cfg_cl FROM cfg_counts),
     (SELECT cfg_all FROM cfg_counts)) AS pools_configured_now
FROM @pub AS p
WHERE p.job_name IN ('@cl_job', '@reserves_job') AND p.target_kind = 'pool'
  AND p.chain_id = @chain AND @window_pub
GROUP BY snapshot_date, job_name
ORDER BY snapshot_date, job_name
