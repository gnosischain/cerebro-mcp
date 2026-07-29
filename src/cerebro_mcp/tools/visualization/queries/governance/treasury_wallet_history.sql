WITH @months_cte,
top_wallets AS (
  SELECT w_chain, w_wallet FROM (
    SELECT t.chain_id AS w_chain, t.wallet_address AS w_wallet,
           max(t.balance_units) AS peak
    FROM @src AS t
    WHERE t.job_name = '@job' AND @chain_sql AND @ltd_sql
      AND t.token_address = @focus_sql AND t.balance_raw != 0
    GROUP BY w_chain, w_wallet
    ORDER BY w_chain, peak DESC, w_wallet
    LIMIT 5 BY w_chain
  )
)
SELECT t.chain_id AS chain_id, m.bucket AS bucket,
       if(tw.w_wallet = '', 'other', t.wallet_address) AS wallet_address,
       max(t.wallet_address IN (@ltd_list)) AS is_ltd,
       sum(t.balance_units) AS units,
       toString(sum(t.balance_raw)) AS units_raw
FROM @src AS t
@months_join
LEFT JOIN top_wallets AS tw
       ON tw.w_chain = t.chain_id AND tw.w_wallet = t.wallet_address
WHERE t.job_name = '@job' AND @chain_sql AND @ltd_sql
  AND t.token_address = @focus_sql AND t.balance_raw != 0
GROUP BY chain_id, bucket, wallet_address
ORDER BY bucket, chain_id, wallet_address
