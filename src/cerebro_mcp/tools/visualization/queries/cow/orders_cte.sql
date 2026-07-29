
og AS (
  SELECT chain_id,uniq(order_uid) AS a,
         minOrNull(creation_date) AS c,maxOrNull(creation_date) AS d,
         maxOrNull(observed_at) AS e
  FROM cow_db.orders
  WHERE environment={env:String} AND chain_id IN (@ids) AND @order_window
  GROUP BY chain_id
),
ogopen AS (
  SELECT chain_id,countIf(status='open') AS b
  FROM (
    SELECT chain_id,order_uid,argMax(status,observed_at) AS status
    FROM cow_db.orders
    WHERE environment={env:String} AND chain_id IN (@ids)
      AND valid_to>toUnixTimestamp(now())
    GROUP BY chain_id,order_uid
  )
  GROUP BY chain_id
)
