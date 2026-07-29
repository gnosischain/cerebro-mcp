
SELECT toStartOfDay(t.block_timestamp) AS bucket,@cid AS chain_id,
       uniq(t.owner) AS active_traders,
       uniqIf(t.owner, toStartOfDay(f.first_seen)=toStartOfDay(t.block_timestamp)) AS new_traders,
       uniq(@trade_key) AS fill_count,
       min(t.block_timestamp) AS indexed_from,
       max(t.block_timestamp) AS indexed_to,
       max(t.observed_at) AS source_observed_at
FROM cow_db.trades AS t
INNER JOIN fs AS f ON f.chain_id=@cid AND f.owner=t.owner
WHERE @arm_where
GROUP BY bucket
