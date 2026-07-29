WITH @months_cte,
@asof_cte_body,
per_bucket AS (
  SELECT t.chain_id AS chain_id, t.token_address AS token_address, m.bucket AS bucket,
         anyHeavy(t.symbol) AS symbol, anyHeavy(t.decimals) AS decimals,
         anyHeavy(t.metadata_status) AS metadata_status,
         sum(t.balance_raw) AS balance_raw_sum,
         sum(t.balance_units) AS balance_units,
         uniqExact(t.wallet_address) AS wallets_holding
  FROM @src AS t
  @months_join
  WHERE t.job_name = '@job' AND @chain_sql AND @asset_sql AND @ltd_sql
    AND t.balance_raw != 0
  GROUP BY chain_id, token_address, bucket
),
held_now AS (
  SELECT t.chain_id AS h_chain, t.token_address AS h_token
  FROM @src AS t
  INNER JOIN asof AS a ON t.chain_id = a.chain_id AND t.snapshot_date = a.as_of
  WHERE t.job_name = '@job' AND @chain_sql AND @asset_sql AND @ltd_sql
    AND t.balance_raw != 0
  GROUP BY h_chain, h_token
),
picked AS (
  SELECT p_chain, p_token FROM (
    SELECT b.chain_id AS p_chain, b.token_address AS p_token,
           uniqExact(b.balance_raw_sum) AS changes
    FROM per_bucket AS b
    INNER JOIN held_now AS h ON h.h_chain = b.chain_id AND h.h_token = b.token_address
    GROUP BY p_chain, p_token
    ORDER BY p_chain, (p_token = @gno_picked_sql) DESC,
             changes DESC, p_token
    LIMIT @history_tokens BY p_chain
  )
)
SELECT b.chain_id AS chain_id, b.bucket AS bucket, b.token_address AS token_address,
       b.symbol AS symbol, b.decimals AS decimals, b.metadata_status AS metadata_status,
       b.balance_units AS balance_units,
       toString(b.balance_raw_sum) AS balance_total_raw,
       b.wallets_holding AS wallets_holding
FROM per_bucket AS b
INNER JOIN picked AS pk ON pk.p_chain = b.chain_id AND pk.p_token = b.token_address
ORDER BY bucket, chain_id, token_address
