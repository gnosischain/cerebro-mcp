
WITH cp AS (
  SELECT chain_id, argMax(block_number, updated_at) AS checkpoint_block,
         max(updated_at) AS checkpoint_updated_at
  FROM cow_db.indexing_checkpoints
  WHERE environment={env:String} AND chain_id IN (@ids) AND source='rpc'
  GROUP BY chain_id
), blocks AS (
  -- block_number IN prunes chain_blocks (sort key) from ~9.2M to ~10 rows;
  -- this runs every 10s, so the full-table join was constant instance load.
  SELECT b.chain_id, argMax(b.block_timestamp, b.observed_at) AS checkpoint_timestamp
  FROM cow_db.chain_blocks AS b
  INNER JOIN cp ON b.chain_id=cp.chain_id AND b.block_number=cp.checkpoint_block
  WHERE b.environment={env:String} AND b.chain_id IN (@ids)
    AND b.block_number IN (SELECT checkpoint_block FROM cp)
  GROUP BY b.chain_id
)
SELECT n.chain_id AS chain_id, cp.checkpoint_block,
       nullIf(blocks.checkpoint_timestamp,toDateTime(0)) AS checkpoint_timestamp,
       cp.checkpoint_updated_at,
       if(blocks.checkpoint_timestamp IS NULL OR blocks.checkpoint_timestamp=toDateTime(0),
          NULL,
          dateDiff('second', blocks.checkpoint_timestamp, now())) AS lag_seconds
FROM (SELECT arrayJoin([@ids]) AS chain_id) AS n
LEFT JOIN cp ON n.chain_id=cp.chain_id
LEFT JOIN blocks ON n.chain_id=blocks.chain_id
ORDER BY n.chain_id
