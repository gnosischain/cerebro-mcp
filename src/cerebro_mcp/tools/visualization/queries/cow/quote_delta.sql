
WITH pol AS (
  SELECT order_uid, tx_hash, log_index, any(policy) AS policy
  FROM cow_db.protocol_fees FINAL
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  GROUP BY order_uid, tx_hash, log_index
)
SELECT policy_family,
       multiIf(delta_bps IS NULL,'unquoted',
               delta_bps< -50,'< -50 bps',
               delta_bps<0,'-50-0 bps',
               delta_bps<10,'0-10 bps',
               delta_bps<50,'10-50 bps',
               delta_bps<200,'50-200 bps','> 200 bps') AS delta_bucket,
       count() AS fills, uniqExact(order_uid) AS orders,
       avgOrNull(delta_bps) AS avg_delta_bps,
       quantile(0.5)(delta_bps) AS median_delta_bps,
       min(block_timestamp) AS indexed_from, max(block_timestamp) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM (
  SELECT q.order_uid AS order_uid,
         multiIf(positionCaseInsensitive(q.policy,'priceImprovement')>0,'price_improvement',
                 positionCaseInsensitive(q.policy,'surplus')>0,'surplus',
                 positionCaseInsensitive(q.policy,'volume')>0,'volume','other') AS policy_family,
         @quote_delta_expr AS delta_bps,
         q.block_timestamp AS block_timestamp, q.observed_at AS observed_at
  FROM (
    SELECT f.order_uid AS order_uid, pol.policy AS policy,
           f.buy_amount AS buy_amount, f.sell_amount AS sell_amount,
           f.block_timestamp AS block_timestamp, f.observed_at AS observed_at
    FROM cow_db.trades AS f
    INNER JOIN pol ON pol.order_uid=f.order_uid AND pol.tx_hash=f.tx_hash
     AND pol.log_index=f.log_index
    WHERE f.environment={env:String} AND f.chain_id={chain_id:UInt64}
      AND f.block_timestamp IS NOT NULL
      AND @fill_time_pred
  ) AS q
)
GROUP BY policy_family, delta_bucket
ORDER BY policy_family, delta_bucket
