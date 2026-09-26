-- The reserves job publishes on its own cadence, so its as-of is resolved
-- separately and bounded by the CL as-of: reserves shown beside a CL snapshot
-- must never post-date it. Same rule as the asof CTE — candidates from the raw
-- publications ledger, completeness from SERVED publications — because the
-- balances view serves only what v_publications_current selects (lesson:
-- published-is-not-served). Consumers carry the matching IN prune against ras_of.
rasof_raw AS (
  SELECT snapshot_date AS rr_date, uniqExact(target_address) AS rr_published
  FROM @pub
  WHERE job_name = '@job' AND target_kind = 'pool' AND chain_id = @chain
    AND snapshot_date <= (SELECT as_of FROM asof)
  GROUP BY rr_date ORDER BY rr_date DESC LIMIT @candidate_days
),
rasof_srv AS (
  SELECT snapshot_date AS rs_date, count() AS rs_served
  FROM @served
  WHERE job_name = '@job' AND target_kind = 'pool' AND chain_id = @chain
    AND snapshot_date IN (SELECT rr_date FROM rasof_raw)
  GROUP BY rs_date
),
rasof_day AS (
  SELECT r.rr_date AS rd_date, s.rs_served AS rd_served, @complete AS rd_complete
  FROM rasof_raw AS r LEFT JOIN rasof_srv AS s ON s.rs_date = r.rr_date
),
rasof AS (
  SELECT if(countIf(rd_complete) > 0, maxIf(rd_date, rd_complete),
            maxIf(rd_date, rd_served > 0)) AS ras_of
  FROM rasof_day
)
