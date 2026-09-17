-- Symbol search, restricted to tokens that actually appear in these pools.
-- Ranked exact / prefix / contains so a search for a short symbol does not bury
-- the exact match under every token containing those letters. Symbols are
-- attacker-authored, so the label carries the address too.
WITH @cfg_cte,
pool_tokens AS (
  SELECT DISTINCT token_address FROM cfg ARRAY JOIN cfg.assets AS token_address
)
SELECT 'token' AS entity_type,
       m.token_address AS identifier,
       concat(ifNull(m.symbol, '?'), ' ', substring(m.token_address, 1, 10), '...',
              substring(m.token_address, 39, 4)) AS label,
       'token' AS role,
       toUInt64(1) AS evidence_count,
       toUInt8(multiIf(lower(ifNull(m.symbol, '')) = lower({q:String}), 0,
                       positionCaseInsensitive(ifNull(m.symbol, ''), {q:String}) = 1, 1,
                       2)) AS match_rank
FROM @db.@meta_view AS m
INNER JOIN pool_tokens AS pt ON pt.token_address = m.token_address
WHERE m.chain_id = @chain
  AND positionCaseInsensitive(ifNull(m.symbol, ''), {q:String}) > 0
ORDER BY match_rank, identifier
