
SELECT bucket, set_events, clear_events, net_change, cumulative_net,
       '@unit' AS bucket_unit
FROM (
  SELECT bucket, set_events, clear_events,
         (set_events - clear_events) AS net_change,
         sum(set_events - clear_events) OVER (ORDER BY bucket) AS cumulative_net
  FROM (
    SELECT @bucket_sql AS bucket,
           countIf(action = 'SetDelegate') AS set_events,
           countIf(action = 'ClearDelegate') AS clear_events
    FROM @src
    WHERE @time_pred
    GROUP BY bucket
  )
)
ORDER BY bucket
