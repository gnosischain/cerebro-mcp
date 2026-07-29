,
np AS (
  SELECT chain_id, token, argMax(native_price, observed_at) AS native_price
  FROM cow_db.native_prices
  WHERE environment={env:String} AND chain_id IN (@ids)
  GROUP BY chain_id, token
)
