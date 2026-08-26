-- Single-chain month-end dates from census_publications — see the per-chain
-- twin and _cte_treasury_asof_per_chain for the rationale, the mandatory
-- `t.snapshot_date IN (SELECT month_end FROM months)` consumer predicate,
-- and why the set is BOUNDED to the latest @history_months month-ends.
WITH months AS (
  SELECT bucket, month_end FROM (
    SELECT toStartOfMonth(snapshot_date) AS bucket,
           max(snapshot_date) AS month_end
    FROM @pub
    WHERE job_name = '@job' AND target_kind = 'token' AND chain_id = @chain
    GROUP BY bucket
    ORDER BY bucket DESC
    LIMIT @history_months
  )
)
