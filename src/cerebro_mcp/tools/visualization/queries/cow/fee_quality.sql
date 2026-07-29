
WITH pol AS (
  SELECT order_uid, tx_hash, log_index, any(policy) AS policy
  FROM cow_db.protocol_fees FINAL
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  GROUP BY order_uid, tx_hash, log_index
)
SELECT multiIf(positionCaseInsensitive(q.policy,'priceImprovement')>0,'price_improvement',
               positionCaseInsensitive(q.policy,'surplus')>0,'surplus',
               positionCaseInsensitive(q.policy,'volume')>0,'volume','other') AS policy_family,
       count() AS fills, uniqExact(q.order_uid) AS orders,
       avg(q.surplus_bps) AS avg_surplus_bps,
       quantile(0.5)(q.surplus_bps) AS median_surplus_bps,
       quantile(0.9)(q.surplus_bps) AS p90_surplus_bps,
       min(q.block_timestamp) AS indexed_from, max(q.block_timestamp) AS indexed_to,
       max(q.observed_at) AS source_observed_at
FROM (
  SELECT f.order_uid AS order_uid, pol.policy AS policy,
         @fill_surplus AS surplus_bps,
         f.block_timestamp AS block_timestamp, f.observed_at AS observed_at
  FROM cow_db.trades AS f
  INNER JOIN pol ON pol.order_uid=f.order_uid AND pol.tx_hash=f.tx_hash
   AND pol.log_index=f.log_index
  INNER JOIN (
    SELECT order_uid,
           argMax(sell_amount,observed_at) AS sell_amount,
           argMax(buy_amount,observed_at) AS buy_amount
    FROM cow_db.orders
    WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    GROUP BY order_uid
  ) AS o ON o.order_uid=f.order_uid
  WHERE f.environment={env:String} AND f.chain_id={chain_id:UInt64}
    AND f.block_timestamp IS NOT NULL
    AND @fill_time_pred
) AS q
GROUP BY policy_family
ORDER BY fills DESC
LIMIT 20
