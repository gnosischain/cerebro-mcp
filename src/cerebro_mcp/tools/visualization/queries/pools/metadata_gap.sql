-- What the plane can and cannot label. Each dimension is counted in the same
-- pass and pivoted, so the numbers cannot drift apart between panels. Read it
-- as the reason prices are shown in raw units almost everywhere: the token
-- metadata this plane resolved covers a small minority of the pool set.
@asof_cte,
@cfg_cte,
@meta_cte,
pooled AS (
  SELECT cfg.pool_family AS pool_family,
         arrayMap(a -> indexOf(mm.mm_tokens, a), cfg.assets) AS idx,
         arrayAll(i -> i > 0 AND mm.mm_decimals[i] >= 0, idx) AS priceable,
         arrayAll(i -> i > 0 AND mm.mm_symbols[i] != '', idx) AS labelled
  FROM cfg CROSS JOIN mm
),
tokens AS (
  SELECT DISTINCT token_address FROM cfg ARRAY JOIN cfg.assets AS token_address
),
token_stats AS (
  SELECT count() AS tok_total,
         countIf(m.symbol IS NOT NULL AND m.symbol != '') AS tok_symbol,
         countIf(m.decimals IS NOT NULL) AS tok_decimals
  FROM tokens AS t
  LEFT JOIN @db.@meta_view AS m
         ON m.chain_id = @chain AND m.token_address = t.token_address
),
pool_stats AS (
  SELECT count() AS pool_total,
         countIf(priceable) AS pool_priceable,
         countIf(labelled) AS pool_labelled
  FROM pooled
)
SELECT dimension, known, unknown, known / nullIf(known + unknown, 0) AS pct_known
FROM (
  SELECT ['tokens_symbol', 'tokens_decimals', 'pools_price_adjustable',
          'pools_fully_labelled'] AS dimensions,
         [(SELECT tok_symbol FROM token_stats), (SELECT tok_decimals FROM token_stats),
          (SELECT pool_priceable FROM pool_stats), (SELECT pool_labelled FROM pool_stats)]
           AS knowns,
         [(SELECT tok_total - tok_symbol FROM token_stats),
          (SELECT tok_total - tok_decimals FROM token_stats),
          (SELECT pool_total - pool_priceable FROM pool_stats),
          (SELECT pool_total - pool_labelled FROM pool_stats)] AS unknowns
)
ARRAY JOIN dimensions AS dimension, knowns AS known, unknowns AS unknown
ORDER BY dimension
