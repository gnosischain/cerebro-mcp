
SELECT toStartOfDay(t.block_timestamp) AS bucket,
       @surplus AS surplus_bps,
       if(o.creation_date<=t.block_timestamp,
          dateDiff('second', o.creation_date, t.block_timestamp), NULL) AS latency_seconds,
       t.block_timestamp, t.observed_at
@quality_join AND @quality_time
