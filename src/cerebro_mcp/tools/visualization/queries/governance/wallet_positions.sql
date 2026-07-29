@asof_cte,
chain_tokens AS (
  SELECT token_address, symbol, total_raw, wallets_holding,
         count() OVER (PARTITION BY symbol) - 1 AS symbol_collisions
  FROM (
    SELECT t.token_address AS token_address, anyHeavy(t.symbol) AS symbol,
           sum(t.balance_raw) AS total_raw,
           uniqExact(t.wallet_address) AS wallets_holding
    FROM @src AS t
    INNER JOIN asof AS a ON t.snapshot_date = a.as_of
    WHERE t.job_name = '@job' AND t.chain_id = @chain
      AND t.balance_raw != 0
    GROUP BY token_address
  )
)
SELECT @chain AS chain_id, w.token_address AS token_address,
       c.symbol AS symbol, w.decimals AS decimals,
       w.metadata_status AS metadata_status,
       c.symbol_collisions AS symbol_collisions,
       c.wallets_holding AS wallets_holding,
       w.balance_total_raw AS balance_total_raw,
       w.balance_units AS balance_units,
       toFloat64(w.balance_raw_sum) / nullIf(toFloat64(c.total_raw), 0)
         AS treasury_share,
       CAST(NULL AS Nullable(Float64)) AS value_usd
FROM (
  SELECT t.token_address AS token_address, anyHeavy(t.decimals) AS decimals,
         anyHeavy(t.metadata_status) AS metadata_status,
         sum(t.balance_raw) AS balance_raw_sum,
         toString(sum(t.balance_raw)) AS balance_total_raw,
         if(anyHeavy(t.decimals) IS NULL, NULL, sum(t.balance_units)) AS balance_units
  FROM @src AS t
  INNER JOIN asof AS a ON t.snapshot_date = a.as_of
  WHERE t.job_name = '@job' AND t.chain_id = @chain
    AND t.wallet_address = {addr:String} AND t.balance_raw != 0
  GROUP BY token_address
) AS w
INNER JOIN chain_tokens AS c ON c.token_address = w.token_address
ORDER BY (w.metadata_status = 'resolved') DESC, c.symbol_collisions ASC,
         w.token_address
