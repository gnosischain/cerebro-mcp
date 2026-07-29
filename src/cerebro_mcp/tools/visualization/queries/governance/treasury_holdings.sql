@asof_cte,
supply AS (
  SELECT s.chain_id AS supply_chain_id,
         s.token_address AS supply_token,
         argMax(s.scalar_raw, s.snapshot_date) AS total_supply_raw
  FROM @scalars AS s
  WHERE s.job_name = '@job' AND s.scalar_name = 'totalSupply'
  GROUP BY s.chain_id, s.token_address
)
SELECT
  t.chain_id AS chain_id,
  t.token_address AS token_address,
  anyHeavy(t.symbol) AS symbol,
  anyHeavy(t.decimals) AS decimals,
  anyHeavy(t.metadata_status) AS metadata_status,
  anyHeavy(t.metadata_status) = 'resolved' AS metadata_known,
  uniqExact(t.wallet_address) AS wallets_holding,
  toString(sum(t.balance_raw)) AS balance_total_raw,
  if(anyHeavy(t.decimals) IS NULL, NULL, sum(t.balance_units)) AS balance_units,
  if(anyHeavy(sp.total_supply_raw) = 0, NULL,
     toFloat64(sum(t.balance_raw)) / toFloat64(anyHeavy(sp.total_supply_raw)))
    AS supply_share,
  CAST(NULL AS Nullable(Float64)) AS value_usd
FROM @src AS t
INNER JOIN asof AS a ON t.chain_id = a.chain_id AND t.snapshot_date = a.as_of
LEFT JOIN supply AS sp
       ON sp.supply_chain_id = t.chain_id AND sp.supply_token = t.token_address
WHERE t.job_name = '@job' AND @chain_sql AND @asset_sql AND @ltd_sql
  AND t.balance_raw != 0
GROUP BY t.chain_id, t.token_address
ORDER BY @sort_fragment
