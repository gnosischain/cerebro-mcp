-- Per-asset symbols aligned to a pool's assets array, resolved through the
-- one-row token-metadata arrays. Two maps rather than one: ClickHouse lambdas
-- cannot bind a local, so the index is computed first and then read. An
-- unresolved symbol comes back as '' and the client renders a short address —
-- the array form cannot carry NULL without breaking alignment with assets.
arrayMap(i -> if(i = 0, '', mm.mm_symbols[i]),
         arrayMap(a -> indexOf(mm.mm_tokens, a), @assets))
