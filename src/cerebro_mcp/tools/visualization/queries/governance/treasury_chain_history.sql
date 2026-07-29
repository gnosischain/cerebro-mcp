WITH @months_cte
SELECT
  t.chain_id AS chain_id,
  m.bucket AS bucket,
  anyHeavy(t.anchor_block) AS anchor_block,
  uniqExactIf(t.token_address, t.balance_raw != 0) AS tokens_held,
  uniqExactIf(t.token_address, t.balance_raw != 0 AND t.metadata_status = 'resolved')
    AS tokens_named,
  uniqExactIf(t.wallet_address, t.balance_raw != 0) AS wallets_holding,
  countIf(t.balance_raw != 0) AS positions,
  sumIf(t.balance_units, t.token_address = @gno_sql) AS gno_units,
  sumIf(t.balance_units, t.token_address = @gno_sql
        AND t.wallet_address NOT IN (@ltd_list)) AS gno_units_ex_ltd
FROM @src AS t
@months_join
WHERE t.job_name = '@job' AND @chain_sql AND @ltd_sql
GROUP BY chain_id, bucket
ORDER BY bucket, chain_id
