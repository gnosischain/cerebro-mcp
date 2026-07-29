
SELECT bucket,
       countIf(kind = 'new') AS new_delegators,
       countIf(kind = 'repointed') AS repointed,
       countIf(kind = 'cleared') AS cleared,
       '@unit' AS bucket_unit
FROM (
  SELECT @bucket_sql AS bucket,
         multiIf(action = 'ClearDelegate', 'cleared',
                 rn = 1, 'new',
                 'repointed') AS kind
  FROM (
    SELECT action, block_timestamp, block_number, log_index,
           row_number() OVER (PARTITION BY chain_id, delegator ORDER BY block_number, log_index) AS rn
    FROM @src
  )
  WHERE @time_pred
)
GROUP BY bucket
ORDER BY bucket
