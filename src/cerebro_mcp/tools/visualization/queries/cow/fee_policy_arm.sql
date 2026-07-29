
SELECT @cid AS chain_id,f.token AS token,f.policy AS policy_raw,
       count() AS fee_entries,uniqExact(f.order_uid) AS orders,
       sum(f.amount) AS amount_sum,
       min(f.observed_at) AS indexed_from,max(f.observed_at) AS indexed_to,
       max(f.observed_at) AS source_observed_at
FROM cow_db.protocol_fees AS f FINAL
WHERE f.environment={env:String} AND f.chain_id=@cid AND @fee_window
GROUP BY f.token,f.policy
