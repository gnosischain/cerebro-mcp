-- The reserves job publishes on its own cadence, so its as-of is resolved
-- separately and bounded by the CL as-of: reserves shown beside a CL snapshot
-- must never post-date it. Same publications-not-views rule as the asof CTE,
-- and consumers carry the matching IN prune against ras_of.
rasof AS (
  SELECT toDate(max(snapshot_date)) AS ras_of
  FROM @pub
  WHERE job_name = '@job' AND target_kind = 'pool' AND chain_id = @chain
    AND snapshot_date <= (SELECT as_of FROM asof)
)
