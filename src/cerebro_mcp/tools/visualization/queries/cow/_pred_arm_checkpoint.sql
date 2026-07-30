-- Reorg guard for one UNION arm: keep only blocks at or below the chain's
-- confirmed checkpoint, read from the `cp` CTE the enclosing statement defines.
--
-- ONLY VALID inside a statement that defines `cp` with a chain_id column (see
-- _cte_checkpoints.sql). Nothing enforces that pairing.
--
-- @chain_id is substituted, not bound: one arm per chain in a single statement.
t.block_number<=(SELECT b FROM cp WHERE cp.chain_id=@chain_id)
