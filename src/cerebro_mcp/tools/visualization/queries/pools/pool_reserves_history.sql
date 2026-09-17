-- Raw token balances held by one pool over time, one series per token. Balances
-- are scaled into units ONLY where the indexer resolved that token's decimals;
-- otherwise balance_units is NULL and the client plots the raw amount with the
-- unresolved marker. token_index keeps the series order stable across days so a
-- token appearing late does not reshuffle the chart.
@asof_cte
SELECT
  toString(b.snapshot_date) AS snapshot_date,
  b.token_address AS token_address,
  dense_rank() OVER (ORDER BY b.token_address) AS token_index,
  m.symbol AS symbol,
  m.decimals AS decimals,
  toString(b.balance_raw) AS balance_raw,
  toFloat64(b.balance_raw) AS balance_float,
  if(m.decimals IS NULL, NULL,
     toFloat64(b.balance_raw) / pow(10, toInt16(m.decimals))) AS balance_units,
  b.anchor_block AS anchor_block
FROM @db.@view AS b
LEFT JOIN @db.@meta_view AS m
       ON m.chain_id = @chain AND m.token_address = b.token_address
WHERE b.chain_id = @chain AND b.job_name = '@job'
  AND b.pool_address = {pool:String} AND @window_state
ORDER BY snapshot_date, token_address
