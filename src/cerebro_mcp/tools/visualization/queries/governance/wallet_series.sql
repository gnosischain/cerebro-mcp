@months_cte,
picked AS (
  SELECT p_token FROM (
    SELECT t.token_address AS p_token, uniqExact(t.balance_raw) AS changes
    FROM @src AS t
    INNER JOIN months AS m ON t.snapshot_date = m.month_end
    WHERE t.job_name = '@job' AND t.chain_id = @chain
      AND t.wallet_address = {addr:String} AND t.balance_raw != 0
    GROUP BY p_token
    ORDER BY (p_token = '@gno') DESC, changes DESC, p_token
    LIMIT @history_tokens
  )
)
SELECT @chain AS chain_id, m.bucket AS bucket,
       t.token_address AS token_address,
       anyHeavy(t.symbol) AS symbol, anyHeavy(t.decimals) AS decimals,
       anyHeavy(t.metadata_status) AS metadata_status,
       sum(t.balance_units) AS balance_units,
       toString(sum(t.balance_raw)) AS balance_total_raw,
       uniqExact(t.wallet_address) AS wallets_holding
FROM @src AS t
INNER JOIN months AS m ON t.snapshot_date = m.month_end
INNER JOIN picked AS pk ON pk.p_token = t.token_address
WHERE t.job_name = '@job' AND t.chain_id = @chain
  AND t.wallet_address = {addr:String} AND t.balance_raw != 0
GROUP BY bucket, token_address
ORDER BY bucket, token_address
