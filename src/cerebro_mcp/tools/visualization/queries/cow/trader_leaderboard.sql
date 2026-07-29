
WITH @shared_ctes
SELECT trader,
       sum(fill_count) AS fill_count,
       sum(settlement_transactions) AS settlement_transactions,
       count() AS chains_active,
       sum(distinct_pairs) AS distinct_pairs,
       min(fs_arm) AS first_seen,
       max(ls_arm) AS last_seen,
       min(fs_arm) AS indexed_from,
       max(ls_arm) AS indexed_to,
       max(obs_arm) AS source_observed_at
FROM (
@leader_union
) GROUP BY trader
ORDER BY fill_count DESC, trader
LIMIT 200
