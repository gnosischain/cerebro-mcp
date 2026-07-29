@asof_cte
SELECT @chain AS chain_id, '@label' AS entity_label,
       t.wallet_address AS wallet_address,
       t.wallet_address IN (@ltd_list) AS is_ltd,
       any(a.as_of) AS as_of,
       anyHeavy(t.anchor_block) AS anchor_block,
       uniqExactIf(t.token_address, t.balance_raw != 0) AS tokens_held,
       uniqExactIf(t.token_address, t.balance_raw != 0
                   AND t.metadata_status = 'resolved') AS tokens_named,
       countIf(t.balance_raw != 0 AND t.metadata_status != 'resolved')
         AS unnamed_positions,
       sumIf(t.balance_units, t.token_address = '@gno') AS gno_units,
       CAST(NULL AS Nullable(Float64)) AS value_usd
FROM @src AS t
INNER JOIN asof AS a ON t.snapshot_date = a.as_of
WHERE t.job_name = '@job' AND t.chain_id = @chain
  AND t.wallet_address = {addr:String}
GROUP BY chain_id, entity_label, wallet_address, is_ltd
ORDER BY wallet_address
