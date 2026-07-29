
om AS (
  SELECT owner,toStartOfMonth(block_timestamp) AS period,max(observed_at) AS obs_at
  FROM cow_db.trades
  WHERE environment={env:String} AND chain_id IN (@ids)
    AND block_timestamp IS NOT NULL
    AND block_timestamp>=toStartOfMonth((SELECT max(a) FROM ta))-toIntervalMonth(@months)
  GROUP BY owner,period
), fsall AS (
  SELECT owner,min(block_timestamp) AS first_seen
  FROM cow_db.trades
  WHERE environment={env:String} AND chain_id IN (@ids)
    AND block_timestamp IS NOT NULL
  GROUP BY owner
)
