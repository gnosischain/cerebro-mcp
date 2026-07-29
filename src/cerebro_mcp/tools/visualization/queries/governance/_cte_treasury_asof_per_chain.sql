WITH asof AS (
  SELECT chain_id, max(snapshot_date) AS as_of
  FROM @src
  WHERE job_name = '@job'
  GROUP BY chain_id
)
