
  SELECT chain_id,order_uid,argMax(status,observed_at) AS status,
         argMax(owner,observed_at) AS owner,
         argMax(creation_date,observed_at) AS creation_date,
         max(observed_at) AS obs_at
  FROM cow_db.orders
  WHERE environment={env:String} AND chain_id IN (@ids) AND @window
  GROUP BY chain_id,order_uid
