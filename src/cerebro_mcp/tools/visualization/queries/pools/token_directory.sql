-- Tokens seen across the pool universe, with how many pools hold each and how
-- far the indexer got resolving its metadata. resolution_status is emitted
-- verbatim: an unresolved token is a fact about the chain, not a gap to paper
-- over, and roughly 98% of the tokens here are in that state.
@asof_cte,
@cfg_cte,
@st_cte,
@probe_cte,
assets AS (
  SELECT cfg.pool_address AS a_pool, token_address
  FROM cfg
  ARRAY JOIN cfg.assets AS token_address
)
SELECT
  a.token_address AS token_address,
  m.symbol AS symbol,
  m.name AS token_name,
  m.decimals AS decimals,
  if(m.resolution_status = '', 'unresolved', m.resolution_status) AS resolution_status,
  m.decimals IS NOT NULL AS is_resolved,
  count() AS pools_count,
  countIf(cfg.pool_family = 'cl') AS cl_pools,
  countIf(cfg.pool_family = 'reserves_only') AS reserves_only_pools,
  countIf(st.liquidity > 0) AS live_pools,
  countIf(pr.ticks_probed) AS probed_pools,
  toString((SELECT as_of FROM asof)) AS as_of
FROM assets AS a
INNER JOIN cfg ON cfg.pool_address = a.a_pool
LEFT JOIN @db.@meta_view AS m
       ON m.chain_id = @chain AND m.token_address = a.token_address
LEFT JOIN st ON st.st_pool = a.a_pool
LEFT JOIN probe AS pr ON pr.p_pool = a.a_pool
WHERE @token_query_sql
GROUP BY token_address, symbol, token_name, decimals, resolution_status, is_resolved
ORDER BY @sort_fragment
