
WITH @shared_ctes,@dynamics_ctes,
monthly AS (
  SELECT o.period AS period,
         count() AS active_traders,
         countIf(toStartOfMonth(f.first_seen)=o.period) AS new_traders,
         countIf(p.owner!='') AS returning_traders,
         countIf(p.owner='' AND toStartOfMonth(f.first_seen)<o.period) AS reactivated_traders,
         max(o.obs_at) AS obs_at
  FROM om AS o
  INNER JOIN fsall AS f ON f.owner=o.owner
  LEFT JOIN (SELECT owner,period+toIntervalMonth(1) AS period FROM om) AS p
    ON p.owner=o.owner AND p.period=o.period
  GROUP BY period
)
SELECT period,active_traders,new_traders,returning_traders,reactivated_traders,
       prev_active-returning_traders AS churned_traders,
       (new_traders+reactivated_traders)/nullIf(prev_active-returning_traders,0) AS quick_ratio,
       returning_traders/nullIf(prev_active,0) AS retention_rate,
       period AS indexed_from,period AS indexed_to,
       obs_at AS source_observed_at
FROM (
  SELECT *,lagInFrame(active_traders,1) OVER (ORDER BY period ASC
           ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS prev_active
  FROM monthly
)
WHERE prev_active>0
ORDER BY period
