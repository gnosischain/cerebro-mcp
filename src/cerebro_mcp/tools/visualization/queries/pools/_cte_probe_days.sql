-- Per-day tick-probe flag for ONE pool, so a history series can grey out the
-- days the indexer did not read the pool's ticks instead of drawing them the
-- same as days it did. Publications only — one pool's whole calendar is about
-- a thousand narrow rows.
probe_days AS (
  SELECT p.snapshot_date AS pd_date,
         NOT has(p.checks_passed, '@check') AS ticks_probed
  FROM @pub AS p
  WHERE p.job_name = '@job' AND p.target_kind = 'pool' AND p.chain_id = @chain
    AND p.target_address = {pool:String}
)
