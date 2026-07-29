@asof_cte
SELECT
  t.chain_id AS chain_id,
  t.wallet_address AS wallet_address,
  t.wallet_address IN (@ltd_list) AS is_ltd,
  uniqExactIf(t.token_address, t.balance_raw != 0) AS tokens_held,
  countIf(t.balance_raw != 0 AND t.metadata_status != 'resolved') AS unnamed_positions,
  sumIf(t.balance_units, t.token_address = @gno_sql) AS gno_units,
  CAST(NULL AS Nullable(Float64)) AS value_usd
FROM @src AS t
INNER JOIN asof AS a ON t.chain_id = a.chain_id AND t.snapshot_date = a.as_of
WHERE t.job_name = '@job' AND @chain_sql AND @ltd_sql
GROUP BY t.chain_id, t.wallet_address
ORDER BY gno_units DESC, tokens_held DESC, wallet_address
