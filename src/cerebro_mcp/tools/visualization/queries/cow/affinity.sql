
WITH @exec_cte,
tt AS (
  SELECT t.owner AS trader, exec.settlement_executor AS settlement_executor,
         count() AS fill_count,
         min(t.block_timestamp) AS indexed_from, max(t.block_timestamp) AS indexed_to,
         max(t.observed_at) AS source_observed_at
  FROM cow_db.trades AS t
  INNER JOIN exec ON exec.tx_hash=t.tx_hash
  WHERE @trade_where
  GROUP BY trader, settlement_executor
),
topt AS (
  SELECT trader FROM tt GROUP BY trader
  ORDER BY sum(fill_count) DESC LIMIT 100
),
tot AS (SELECT sum(fill_count) AS all_fills FROM tt),
sol AS (
  SELECT settlement_executor, sum(fill_count) AS solver_fills
  FROM tt GROUP BY settlement_executor
)
SELECT tt.trader AS trader, tt.settlement_executor AS settlement_executor,
       tt.fill_count AS fill_count,
       tt.fill_count/sum(tt.fill_count) OVER (PARTITION BY tt.trader) AS trader_share,
       sol.solver_fills/(SELECT all_fills FROM tot) AS solver_global_share,
       tt.indexed_from AS indexed_from, tt.indexed_to AS indexed_to,
       tt.source_observed_at AS source_observed_at
FROM tt
INNER JOIN topt USING (trader)
INNER JOIN sol USING (settlement_executor)
ORDER BY tt.trader, tt.fill_count DESC
LIMIT 2000
