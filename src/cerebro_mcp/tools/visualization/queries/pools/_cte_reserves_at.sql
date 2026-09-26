-- Raw token balances at the reserves as-of, folded to one row per pool so a
-- Balancer pool holding eight assets costs one row rather than eight. Balances
-- stay UInt256 strings: without decimals there is no honest unit to scale them
-- into, and the client renders raw amounts with the unresolved marker.
res AS (
  SELECT b.pool_address AS r_pool,
         any(b.snapshot_date) AS r_date,
         groupArray(b.token_address) AS r_tokens,
         groupArray(toString(b.balance_raw)) AS r_balances,
         max(b.balance_raw > 0) AS r_any_positive
  FROM @db.@view AS b
  WHERE b.chain_id = @chain AND b.job_name = '@job'
    AND b.snapshot_date = (SELECT ras_of FROM rasof)
    AND @pool_sql
  GROUP BY r_pool
)
