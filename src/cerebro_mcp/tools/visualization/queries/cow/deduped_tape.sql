
SELECT u.chain_id AS chain_id,u.tx_hash AS tx_hash,u.log_index AS log_index,
       u.order_uid AS order_uid,
       argMax(u.block_timestamp,u.observed_at) AS block_timestamp,
       argMax(u.owner,u.observed_at) AS owner,
       argMax(u.sell_token,u.observed_at) AS sell_token,
       argMax(u.buy_token,u.observed_at) AS buy_token,
       argMax(u.sell_amount,u.observed_at) AS sell_amount,
       argMax(u.buy_amount,u.observed_at) AS buy_amount,
       argMax(u.fee_amount,u.observed_at) AS fee_amount,
       argMax(u.source,u.observed_at) AS source,
       max(u.observed_at) AS obs_at
FROM (
  SELECT chain_id,tx_hash,log_index,order_uid,block_timestamp,block_number,
         owner,sell_token,buy_token,sell_amount,buy_amount,fee_amount,
         source,observed_at
  FROM cow_db.trades AS t
  WHERE environment={env:String} AND chain_id IN (@ids)
    AND block_timestamp IS NOT NULL AND @time_window@extra
  ORDER BY block_timestamp DESC
  LIMIT @tape_arm_limit
) AS u
INNER JOIN cp ON cp.chain_id=u.chain_id
WHERE u.block_number<=cp.b
GROUP BY u.chain_id,u.tx_hash,u.log_index,u.order_uid
ORDER BY block_timestamp DESC
LIMIT @row_cap
