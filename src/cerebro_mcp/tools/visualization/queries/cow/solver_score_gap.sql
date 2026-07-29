
WITH blk AS (
  SELECT chain_id, block_number,
         argMax(block_timestamp, observed_at) AS block_timestamp
  FROM cow_db.chain_blocks
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND block_number IN (
      SELECT auction_block FROM cow_db.solver_competitions FINAL
      WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    )
  GROUP BY chain_id, block_number
)
SELECT c.auction_id AS auction_id,
       b.block_timestamp AS auction_timestamp,
       toFloat64OrNull(s.score) AS winning_score,
       toFloat64OrNull(JSONExtractString(c.reference_score,{id:String})) AS reference_score,
       toFloat64OrNull(s.score)
         - toFloat64OrNull(JSONExtractString(c.reference_score,{id:String})) AS score_gap,
       (toFloat64OrNull(s.score) IS NOT NULL
        AND toFloat64OrNull(JSONExtractString(c.reference_score,{id:String})) IS NOT NULL) AS scores_parsed,
       s.observed_at AS source_observed_at
FROM cow_db.solver_competitions AS c FINAL
INNER JOIN cow_db.competition_solutions AS s FINAL
  ON s.environment=c.environment AND s.chain_id=c.chain_id
 AND s.auction_id=c.auction_id AND s.is_winner AND s.solver={id:String}
LEFT JOIN blk AS b ON b.chain_id=c.chain_id AND b.block_number=c.auction_block
WHERE c.environment={env:String} AND c.chain_id={chain_id:UInt64}
ORDER BY auction_timestamp DESC, auction_id DESC
LIMIT 500
