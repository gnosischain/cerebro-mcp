
WITH cp AS (
  SELECT chain_id, argMax(block_number, updated_at) AS checkpoint_block,
         max(updated_at) AS checkpoint_updated_at
  FROM cow_db.indexing_checkpoints
  WHERE @scope_pred AND source='rpc'
  GROUP BY chain_id
), blocks AS (
  -- block_number IS the sort key → this IN-set prunes chain_blocks from the
  -- whole ~9.2M-row table to the ~10 checkpoint blocks. Without it the JOIN
  -- condition alone forces a full-table scan + hash. (No FINAL: argMax dedups.)
  SELECT b.chain_id,b.block_number,
         argMax(b.block_timestamp,b.observed_at) AS checkpoint_timestamp
  FROM cow_db.chain_blocks AS b
  INNER JOIN cp
    ON b.chain_id=cp.chain_id AND b.block_number=cp.checkpoint_block
  WHERE b.environment={env:String} AND b.chain_id IN (@ids)
    AND b.block_number IN (SELECT checkpoint_block FROM cp)
  GROUP BY b.chain_id,b.block_number
), obs AS (
  -- max(observed_at) is dedup-invariant; the base table avoids expanding the
  -- canonical view (FINAL + chain_blocks join) once per chain.
  SELECT chain_id, max(observed_at) AS trade_observed_at
  FROM cow_db.trades WHERE @scope_pred GROUP BY chain_id
), ord AS (
  -- max(observed_at) is FINAL-invariant on a ReplacingMergeTree(observed_at);
  -- skipping FINAL avoids the merge cost on the largest per-chain table.
  SELECT chain_id, max(observed_at) AS order_observed_at
  FROM cow_db.orders WHERE @scope_pred GROUP BY chain_id
), comp AS (
  SELECT chain_id, max(auction_block) AS max_competition_block,
         max(observed_at) AS competition_observed_at
  FROM cow_db.solver_competitions FINAL WHERE @scope_pred GROUP BY chain_id
), np AS (
  -- native_prices keeps observed_at in its sort key (time series); FINAL does
  -- not collapse snapshots there, so a plain max() is the correct read.
  SELECT chain_id, max(observed_at) AS native_price_observed_at
  FROM cow_db.native_prices WHERE @scope_pred GROUP BY chain_id
)
SELECT n.chain_id AS chain_id, cp.checkpoint_block,
       nullIf(blocks.checkpoint_timestamp,toDateTime(0)) AS checkpoint_timestamp,
       cp.checkpoint_updated_at, obs.trade_observed_at, ord.order_observed_at,
       comp.max_competition_block,comp.competition_observed_at,
       np.native_price_observed_at,
       greatest(obs.trade_observed_at,ord.order_observed_at,
                comp.competition_observed_at,np.native_price_observed_at) AS source_observed_at
FROM (SELECT arrayJoin([@ids]) AS chain_id) AS n
LEFT JOIN cp ON n.chain_id=cp.chain_id
LEFT JOIN blocks ON cp.chain_id=blocks.chain_id AND cp.checkpoint_block=blocks.block_number
LEFT JOIN obs ON n.chain_id=obs.chain_id
LEFT JOIN ord ON n.chain_id=ord.chain_id
LEFT JOIN comp ON n.chain_id=comp.chain_id
LEFT JOIN np ON n.chain_id=np.chain_id
ORDER BY n.chain_id
