months AS (
  SELECT chain_id, toStartOfMonth(snapshot_date) AS bucket,
         max(snapshot_date) AS month_end
  FROM @src
  WHERE job_name = '@job'
  GROUP BY chain_id, bucket
)
