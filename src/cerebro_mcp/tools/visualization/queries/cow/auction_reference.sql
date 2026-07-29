
WITH @token_metadata_cte, bp AS (
  SELECT auction_id, argMax(price,observed_at) AS base_price,
         max(observed_at) AS base_observed_at
  FROM cow_db.auction_prices
  WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND token={base:String}
  GROUP BY auction_id
), qp AS (
  SELECT auction_id, argMax(price,observed_at) AS quote_price,
         max(observed_at) AS quote_observed_at
  FROM cow_db.auction_prices
  WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND token={quote:String}
  GROUP BY auction_id
), comp AS (
  SELECT auction_id, argMax(auction_block,observed_at) AS auction_block,
         max(observed_at) AS competition_observed_at
  FROM cow_db.solver_competitions FINAL
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  GROUP BY auction_id
), blocks AS (
  -- Only the auction blocks (thousands, index-lookup) instead of the whole
  -- chain_blocks table with FINAL (millions of rows — a prior OOM source).
  SELECT block_number,argMax(block_timestamp,observed_at) AS auction_timestamp
  FROM cow_db.chain_blocks
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND block_number IN (SELECT auction_block FROM comp)
  GROUP BY block_number
)
SELECT bp.auction_id, blocks.auction_timestamp,
       toFloat64(bp.base_price)/nullIf(toFloat64(qp.quote_price),0)
         * pow(10,toFloat64((SELECT anyOrNull(decimals) FROM tm WHERE token={base:String}))
                  -toFloat64((SELECT anyOrNull(decimals) FROM tm WHERE token={quote:String}))) AS price,
       greatest(bp.base_observed_at,qp.quote_observed_at,comp.competition_observed_at) AS source_observed_at,
       blocks.auction_timestamp AS indexed_from, blocks.auction_timestamp AS indexed_to
FROM bp INNER JOIN qp USING auction_id
LEFT JOIN comp USING auction_id
LEFT JOIN blocks ON comp.auction_block=blocks.block_number
WHERE blocks.block_number!=0 AND @auction_time
ORDER BY blocks.auction_timestamp
