
WITH @shared_ctes,@dynamics_ctes,
coh AS (
  SELECT owner,toStartOfMonth(first_seen) AS cohort_month
  FROM fsall
  WHERE first_seen>=toStartOfMonth((SELECT max(a) FROM ta))-toIntervalMonth(@dynamics_months)
), csize AS (
  SELECT cohort_month,uniqExact(owner) AS cohort_size FROM coh GROUP BY cohort_month
)
SELECT c.cohort_month AS cohort_month,
       dateDiff('month',c.cohort_month,om.period) AS month_index,
       any(cs.cohort_size) AS cohort_size,
       uniqExact(om.owner) AS active_traders,
       uniqExact(om.owner)/nullIf(any(cs.cohort_size),0) AS retention_share,
       c.cohort_month AS indexed_from,max(om.period) AS indexed_to,
       max(om.obs_at) AS source_observed_at
FROM om
INNER JOIN coh AS c ON c.owner=om.owner
INNER JOIN csize AS cs ON cs.cohort_month=c.cohort_month
WHERE om.period>=c.cohort_month
GROUP BY cohort_month,month_index
ORDER BY cohort_month,month_index
