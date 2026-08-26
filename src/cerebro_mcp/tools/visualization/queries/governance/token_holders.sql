@asof_cte
SELECT @chain AS chain_id, wallet_address, is_ltd,
       balance_total_raw, balance_units,
       toFloat64(balance_raw_sum)
         / nullIf(sum(toFloat64(balance_raw_sum)) OVER (), 0) AS treasury_share,
       CAST(NULL AS Nullable(Float64)) AS value_usd
FROM (
  SELECT t.wallet_address AS wallet_address,
         t.wallet_address IN (@ltd_list) AS is_ltd,
         sum(t.balance_raw) AS balance_raw_sum,
         toString(sum(t.balance_raw)) AS balance_total_raw,
         if(anyHeavy(t.decimals) IS NULL, NULL, sum(t.balance_units)) AS balance_units
  FROM @src AS t
  INNER JOIN asof AS a ON t.snapshot_date = a.as_of
  WHERE t.job_name = '@job' AND t.snapshot_date IN (SELECT as_of FROM asof)
    AND t.chain_id = @chain
    AND t.token_address = {addr:String} AND t.balance_raw != 0
  GROUP BY wallet_address, is_ltd
)
ORDER BY balance_raw_sum DESC, wallet_address
