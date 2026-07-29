WITH months AS (
  SELECT toStartOfMonth(snapshot_date) AS bucket,
         max(snapshot_date) AS month_end
  FROM @src
  WHERE job_name = '@job' AND chain_id = @chain
  GROUP BY bucket
)
