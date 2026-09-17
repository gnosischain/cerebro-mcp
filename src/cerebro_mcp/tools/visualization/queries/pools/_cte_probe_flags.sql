-- Per-pool publication facts at the resolved as-of. The load-bearing column is
-- ticks_probed: the indexer only reads a pool's initialized ticks when it is
-- above an activity threshold, and records that decision by adding the
-- below-threshold check to checks_passed. A pool without it has state but no
-- tick-level profile, which is a coverage fact the UI must show as a badge
-- rather than an empty chart.
probe AS (
  SELECT p.target_address AS p_pool,
         NOT has(p.checks_passed, '@check') AS ticks_probed,
         p.anchor_block AS p_anchor_block,
         p.published_at AS p_published_at
  FROM @pub AS p
  WHERE p.job_name = '@job' AND p.target_kind = 'pool' AND p.chain_id = @chain
    AND p.snapshot_date IN (SELECT as_of FROM asof)
)
