
  SELECT chain_id,order_uid,argMax(class,observed_at) AS order_class,
         argMax(kind,observed_at) AS order_kind,
         argMax(signing_scheme,observed_at) AS signing_scheme,
         argMax(partially_fillable,observed_at) AS partially_fillable,
         argMax(status,observed_at) AS status,
         argMax(owner,observed_at) AS owner,
         argMax(creation_date,observed_at) AS creation_date,
         max(observed_at) AS obs_at
  FROM cow_db.orders
  WHERE environment={env:String} AND chain_id IN (@ids) AND @order_window
  GROUP BY chain_id,order_uid
