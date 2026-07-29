
WITH @remaining_cte
SELECT side,limit_price,sum(base_remaining) AS base_quantity,count() AS intent_count,
       min(creation_date) AS indexed_from,max(creation_date) AS indexed_to,
       max(observed_at) AS source_observed_at
FROM normalized
WHERE isFinite(limit_price) AND limit_price>0
GROUP BY side,limit_price
ORDER BY side,if(side='bid',-limit_price,limit_price)
