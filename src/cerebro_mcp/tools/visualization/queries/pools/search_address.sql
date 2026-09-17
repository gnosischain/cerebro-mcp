-- Exact-address resolution. Config and metadata only: neither arm touches a
-- v_pool_* view, so a search costs one small scan regardless of how much
-- history the plane holds. A pool address and a token address are both
-- returned when an address is somehow both, rather than the first match
-- winning silently.
WITH @cfg_cte
SELECT 'pool' AS entity_type,
       cfg.pool_address AS identifier,
       concat(cfg.pool_class, ' ', substring(cfg.pool_address, 1, 10), '...',
              substring(cfg.pool_address, 39, 4)) AS label,
       cfg.pool_family AS role,
       toUInt64(cfg.n_assets) AS evidence_count,
       toUInt8(0) AS match_rank
FROM cfg
WHERE cfg.pool_address = {q:String}
UNION ALL
SELECT 'token' AS entity_type,
       {q:String} AS identifier,
       concat('token ', substring({q:String}, 1, 10), '...',
              substring({q:String}, 39, 4)) AS label,
       'token' AS role,
       count() AS evidence_count,
       toUInt8(0) AS match_rank
FROM cfg
WHERE has(cfg.assets, {q:String})
HAVING evidence_count > 0
