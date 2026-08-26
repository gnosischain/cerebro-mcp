@asof_cte,
held AS (
  SELECT t.token_address AS token_address,
         anyHeavy(t.symbol) AS symbol,
         anyHeavy(t.decimals) AS decimals,
         anyHeavy(t.metadata_status) AS metadata_status,
         anyHeavy(t.anchor_block) AS anchor_block,
         any(a.as_of) AS as_of,
         uniqExact(t.wallet_address) AS wallets_holding,
         sum(t.balance_raw) AS balance_raw_sum,
         if(anyHeavy(t.decimals) IS NULL, NULL, sum(t.balance_units)) AS balance_units
  FROM @src AS t
  INNER JOIN asof AS a ON t.snapshot_date = a.as_of
  WHERE t.job_name = '@job' AND t.snapshot_date IN (SELECT as_of FROM asof)
    AND t.chain_id = @chain AND t.balance_raw != 0
  GROUP BY token_address
),
supply AS (
  SELECT s.token_address AS supply_token,
         argMax(s.scalar_raw, s.snapshot_date) AS total_supply_raw
  FROM @scalars AS s
  -- Date prune is load-bearing — see treasury_holdings.sql's supply CTE.
  WHERE s.job_name = '@job' AND s.chain_id = @chain
    AND s.scalar_name = 'totalSupply' AND s.token_address = {addr:String}
    AND s.snapshot_date IN (SELECT as_of FROM asof)
  GROUP BY supply_token
)
SELECT @chain AS chain_id, '@label' AS entity_label,
       h.token_address AS token_address, h.symbol AS symbol,
       h.decimals AS decimals, h.metadata_status AS metadata_status,
       h.as_of AS as_of, h.anchor_block AS anchor_block,
       h.wallets_holding AS wallets_holding,
       toString(h.balance_raw_sum) AS balance_total_raw,
       h.balance_units AS balance_units,
       h.symbol_collisions AS symbol_collisions,
       if(anyHeavy(sp.total_supply_raw) = 0, NULL,
          toFloat64(h.balance_raw_sum) / toFloat64(anyHeavy(sp.total_supply_raw)))
         AS supply_share,
       CAST(NULL AS Nullable(Float64)) AS value_usd
FROM (
  SELECT *, count() OVER (PARTITION BY symbol) - 1 AS symbol_collisions FROM held
) AS h
LEFT JOIN supply AS sp ON sp.supply_token = h.token_address
WHERE h.token_address = {addr:String}
GROUP BY chain_id, entity_label, token_address, symbol, decimals, metadata_status,
         as_of, anchor_block, wallets_holding, balance_total_raw, balance_units,
         symbol_collisions, h.balance_raw_sum
ORDER BY token_address
