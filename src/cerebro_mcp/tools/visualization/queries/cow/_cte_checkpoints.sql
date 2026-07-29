
cp AS (
  SELECT chain_id, argMax(block_number,updated_at) AS b
  FROM cow_db.indexing_checkpoints
  WHERE environment={env:String} AND chain_id IN (@ids) AND source='rpc'
  GROUP BY chain_id
), ta AS (
  SELECT chain_id, max(block_timestamp) AS a
  FROM cow_db.trades
  WHERE environment={env:String} AND chain_id IN (@ids)
  GROUP BY chain_id
)
