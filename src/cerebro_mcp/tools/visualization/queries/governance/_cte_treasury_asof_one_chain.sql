WITH asof AS (
  SELECT max(snapshot_date) AS as_of
  FROM @src
  WHERE job_name = '@job' AND chain_id = @chain
)
