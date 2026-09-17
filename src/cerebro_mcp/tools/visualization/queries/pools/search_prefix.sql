-- Partial-address search, for the common case of pasting a truncated address
-- out of a block explorer. Prefix only, never a substring scan: a substring
-- match over 4,022 pools and 3,398 tokens returns noise sorted by nothing.
WITH @cfg_cte
SELECT 'pool' AS entity_type,
       cfg.pool_address AS identifier,
       concat(cfg.pool_class, ' ', substring(cfg.pool_address, 1, 10), '...',
              substring(cfg.pool_address, 39, 4)) AS label,
       cfg.pool_family AS role,
       toUInt64(cfg.n_assets) AS evidence_count,
       toUInt8(1) AS match_rank
FROM cfg
WHERE startsWith(cfg.pool_address, {q:String})
UNION ALL
SELECT 'token' AS entity_type,
       token_address AS identifier,
       concat('token ', substring(token_address, 1, 10), '...',
              substring(token_address, 39, 4)) AS label,
       'token' AS role,
       count() AS evidence_count,
       toUInt8(1) AS match_rank
FROM cfg
ARRAY JOIN cfg.assets AS token_address
WHERE startsWith(token_address, {q:String})
GROUP BY identifier, label, role
