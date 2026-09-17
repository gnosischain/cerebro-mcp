-- Token symbols and decimals for the whole chain, collapsed into three parallel
-- arrays in ONE row so a pool's N assets can be labelled with a single
-- CROSS JOIN instead of one LEFT JOIN per asset position. The three groupArray
-- calls share one aggregation pass over the same rows, so their element order
-- is identical by construction.
--
-- Sentinels are INTERNAL and never reach a result column: '' means "symbol not
-- observed" and -1 means "decimals not observed". Both are converted back to
-- NULL in every projection. They exist only because groupArray drops NULL
-- inputs, which would silently misalign the arrays. Decimals are never guessed:
-- a pool whose decimals are unknown gets a NULL adjusted price, not a plausible
-- wrong number.
mm AS (
  SELECT groupArray(m.token_address) AS mm_tokens,
         groupArray(ifNull(m.symbol, '')) AS mm_symbols,
         groupArray(toInt16(ifNull(m.decimals, -1))) AS mm_decimals
  FROM @db.@meta_view AS m
  WHERE m.chain_id = @chain
)
