-- Two independent clocks, one per indexer job. The CL job and the reserves job
-- publish on their own cadences, so a single "latest snapshot" would hide one
-- of them falling behind. Publications only — the views cannot answer this
-- cheaply and do not need to.
WITH latest AS (
  SELECT job_name AS l_job, max(snapshot_date) AS l_date
  FROM @pub
  WHERE job_name IN ('@cl_job', '@reserves_job') AND target_kind = 'pool'
    AND chain_id = @chain
  GROUP BY l_job
)
SELECT
  if(p.job_name = '@cl_job', 'cl_state', 'reserves') AS source,
  toString(p.snapshot_date) AS latest_snapshot_date,
  max(p.anchor_block) AS latest_anchor_block,
  uniqExact(p.target_address) AS pools_published,
  max(p.published_at) AS latest_published_at
FROM @pub AS p
INNER JOIN latest AS l ON l.l_job = p.job_name AND l.l_date = p.snapshot_date
WHERE p.job_name IN ('@cl_job', '@reserves_job') AND p.target_kind = 'pool'
  AND p.chain_id = @chain
  AND p.snapshot_date IN (SELECT l_date FROM latest)
GROUP BY source, latest_snapshot_date
ORDER BY source
