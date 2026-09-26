-- Per-pool publication facts at the resolved as-of. The load-bearing column is
-- ticks_probed: the indexer only reads a pool's initialized ticks when it is
-- above an activity threshold, and records that decision by adding the
-- below-threshold check to checks_passed. A pool without it has state but no
-- tick-level profile, which is a coverage fact the UI must show as a badge
-- rather than an empty chart.
--
-- Read from the SERVED attempt, one row per pool. census_publications holds a
-- row per attempt that wrote the day, and a re-census leaves a second one (up to
-- 247 extra CL pools a day on 2026-09-08..11). Every consumer LEFT JOINs this CTE
-- by pool, so an unpinned read fanned each such pool into two joined rows —
-- pools_summary counted 2,714 CL pools on 2026-09-10 against 2,521 configured.
-- Pinning to the attempt v_publications_current selects (the attempt the state
-- view's numbers come from) and LIMIT 1 BY make the join 1:1 by construction.
-- Lesson: published-is-not-served.
probe AS (
  SELECT p.target_address AS p_pool,
         NOT has(p.checks_passed, '@check') AS ticks_probed,
         p.anchor_block AS p_anchor_block,
         p.published_at AS p_published_at
  FROM @pub AS p
  WHERE p.job_name = '@job' AND p.target_kind = 'pool' AND p.chain_id = @chain
    AND p.snapshot_date = (SELECT as_of FROM asof)
    AND (p.target_address, p.attempt_id) IN (
      SELECT v.target_address, v.attempt_id FROM @served AS v
      WHERE v.job_name = '@job' AND v.target_kind = 'pool' AND v.chain_id = @chain
        AND v.snapshot_date = (SELECT as_of FROM asof))
  ORDER BY p_pool, p_published_at DESC LIMIT 1 BY p_pool
)
