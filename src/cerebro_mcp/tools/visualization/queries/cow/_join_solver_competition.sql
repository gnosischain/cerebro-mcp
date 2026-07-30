-- The solver-view join spine: competition solutions to their competition, then
-- to the prefiltered block-time lookup.
--
-- FINAL on both cow_db tables is CORRECT and must not be removed — both are raw
-- ReplacingMergeTree and are re-inserted as a competition is observed. See
-- ch-final-three-way-rule.
--
-- `blk` is joined, NOT cow_db.chain_blocks directly: joining the full
-- chain_blocks table (with FINAL) builds a hash table of millions of rows per
-- chain. Restricting to blocks that actually appear as auction blocks keeps the
-- join tiny in both single-chain and all-networks mode — see solver_blocks_cte.sql.
--
-- LEFT (not INNER) on blk: a competition whose auction_block is not yet in
-- chain_blocks must still be counted, with a NULL timestamp, rather than vanish.
-- Callers pair this with `b.block_number!=0` in their WHERE.
--
-- ONLY VALID inside a statement that defines `blk`.
FROM cow_db.competition_solutions AS s FINAL
INNER JOIN cow_db.solver_competitions AS c FINAL
  ON s.environment=c.environment AND s.chain_id=c.chain_id AND s.auction_id=c.auction_id
LEFT JOIN blk AS b
  ON b.chain_id=c.chain_id AND b.block_number=c.auction_block
