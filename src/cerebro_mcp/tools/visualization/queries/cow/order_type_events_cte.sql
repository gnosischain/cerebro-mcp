
oea AS (
  SELECT max(coalesce(event_timestamp,observed_at)) AS a FROM cow_db.order_events
  WHERE environment={env:String} AND chain_id IN (@ids)
)
