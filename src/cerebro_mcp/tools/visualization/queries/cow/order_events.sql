
SELECT event_id,event_type,owner,block_number,transaction_hash,log_index,event_timestamp,
       payload,source,observed_at AS source_observed_at
FROM cow_db.order_events FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND order_uid={id:String}
ORDER BY coalesce(event_timestamp,observed_at),event_id
