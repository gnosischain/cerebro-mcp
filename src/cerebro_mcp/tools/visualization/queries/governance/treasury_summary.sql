@asof_cte
SELECT
  t.chain_id AS chain_id,
  a.as_of AS as_of,
  anyHeavy(t.anchor_block) AS anchor_block,
  anyHeavy(t.anchor_hash) AS anchor_hash,
  uniqExactIf(t.token_address, t.balance_raw != 0) AS tokens_held,
  uniqExact(t.wallet_address) AS wallets_tracked,
  countIf(t.balance_raw != 0) AS positions,
  uniqExactIf(t.token_address, t.balance_raw != 0 AND t.metadata_status = 'resolved')
    AS tokens_named,
  sumIf(t.balance_units, t.token_address = @gno_sql) AS gno_units,
  sumIf(t.balance_units, t.token_address = @gno_sql
        AND t.wallet_address NOT IN (@ltd_list)) AS gno_units_ex_ltd,
  uniqExactIf(t.token_address, t.balance_raw != 0 AND t.metadata_status = 'resolved')
    / nullIf(uniqExactIf(t.token_address, t.balance_raw != 0), 0) AS metadata_known_share,
  CAST(NULL AS Nullable(Float64)) AS nav_usd
FROM @src AS t
INNER JOIN asof AS a ON t.chain_id = a.chain_id AND t.snapshot_date = a.as_of
-- The IN is the prune (folds to constants; the join never prunes the view).
WHERE t.job_name = '@job' AND t.snapshot_date IN (SELECT as_of FROM asof)
  AND @chain_sql AND @ltd_sql
GROUP BY t.chain_id, a.as_of
ORDER BY as_of DESC, chain_id
