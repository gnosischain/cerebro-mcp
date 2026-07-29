
WITH @remaining_cte
SELECT side,count() AS intent_count,sum(base_remaining) AS base_remaining,
       min(creation_date) AS indexed_from,max(creation_date) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM normalized
GROUP BY side
ORDER BY side
