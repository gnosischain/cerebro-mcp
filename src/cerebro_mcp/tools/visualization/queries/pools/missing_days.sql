-- Days the indexer skipped or only partly covered. A calendar is generated
-- between each job's first and last publication so an ABSENT day is a row
-- rather than a gap the eye has to notice; a PARTIAL day is one that published
-- far fewer pools than the week before it, which is how a half-finished run
-- shows up. Both kinds are named, never merged into one count.
@asof_cte,
daily AS (
  SELECT p.job_name AS d_job, p.snapshot_date AS d_date,
         uniqExact(p.target_address) AS pools_published
  FROM @pub AS p
  WHERE p.job_name IN ('@cl_job', '@reserves_job') AND p.target_kind = 'pool'
    AND p.chain_id = @chain
  GROUP BY d_job, d_date
),
bounds AS (
  SELECT d_job AS b_job, min(d_date) AS b_start, max(d_date) AS b_end
  FROM daily GROUP BY b_job
),
calendar AS (
  SELECT b_job AS c_job,
         b_start + toIntervalDay(arrayJoin(range(0, toUInt32(b_end - b_start) + 1)))
           AS c_date
  FROM bounds
),
expected AS (
  SELECT d_job AS e_job, d_date AS e_date, pools_published,
         max(pools_published) OVER (PARTITION BY d_job ORDER BY d_date
           ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING) AS prior_peak
  FROM daily
)
SELECT
  toString(cal.c_date) AS snapshot_date,
  cal.c_job AS job_name,
  if(e.e_date IS NULL OR e.pools_published = 0, 'absent', 'partial') AS gap_kind,
  e.pools_published AS pools_published,
  ifNull(e.prior_peak, 0) AS expected_pools
FROM calendar AS cal
LEFT JOIN expected AS e ON e.e_job = cal.c_job AND e.e_date = cal.c_date
WHERE e.e_date IS NULL
   OR (e.prior_peak > 0 AND e.pools_published < e.prior_peak * 0.9)
ORDER BY snapshot_date DESC, job_name
