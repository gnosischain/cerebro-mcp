
oa AS (
  SELECT chain_id, max(creation_date) AS a
  FROM cow_db.orders
  WHERE environment={env:String} AND chain_id IN (@ids)
  GROUP BY chain_id
)
