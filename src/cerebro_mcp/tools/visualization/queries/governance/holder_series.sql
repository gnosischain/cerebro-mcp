@months_cte,
top_wallets AS (
  SELECT w_wallet FROM (
    SELECT t.wallet_address AS w_wallet, max(t.balance_units) AS peak
    FROM @src AS t
    WHERE t.job_name = '@job' AND t.chain_id = @chain
      AND t.token_address = {addr:String} AND t.balance_raw != 0
    GROUP BY w_wallet
    ORDER BY peak DESC, w_wallet
    LIMIT 6
  )
)
SELECT @chain AS chain_id, m.bucket AS bucket,
       if(tw.w_wallet = '', 'other', t.wallet_address) AS wallet_address,
       max(t.wallet_address IN (@ltd_list)) AS is_ltd,
       sum(t.balance_units) AS units,
       toString(sum(t.balance_raw)) AS units_raw
FROM @src AS t
INNER JOIN months AS m ON t.snapshot_date = m.month_end
LEFT JOIN top_wallets AS tw ON tw.w_wallet = t.wallet_address
WHERE t.job_name = '@job' AND t.chain_id = @chain
  AND t.token_address = {addr:String} AND t.balance_raw != 0
GROUP BY bucket, wallet_address
ORDER BY bucket, wallet_address
