-- Per-asset decimals aligned to a pool's assets array. -1 is the internal
-- not-observed marker carried by the metadata arrays; it is converted to NULL
-- wherever a single token's decimals are projected, and the client treats a
-- negative entry here the same way.
arrayMap(i -> if(i = 0, toInt16(-1), mm.mm_decimals[i]),
         arrayMap(a -> indexOf(mm.mm_tokens, a), @assets))
