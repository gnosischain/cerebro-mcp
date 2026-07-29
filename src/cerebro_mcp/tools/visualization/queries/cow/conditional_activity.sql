WITH @oea_cte
SELECT toStartOfDay(@event_ts) AS bucket,chain_id,event_type,
       uniq(event_id) AS events,uniq(owner) AS creators,
       min(@event_ts) AS indexed_from,max(@event_ts) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM cow_db.order_events
WHERE environment={env:String} AND chain_id IN (@ids)
  AND event_type IN ('ConditionalOrderCreated','MerkleRootSet','OrderInvalidation','SwapGuardSet')
  AND @event_window
GROUP BY bucket,chain_id,event_type
ORDER BY bucket,chain_id,event_type
