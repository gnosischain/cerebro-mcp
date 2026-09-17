-- First seen / last seen / days observed per pool, from publications rather
-- than from any view: this is the one aggregate that must span all history, and
-- publications is the only relation on the plane cheap enough to scan whole.
-- Both jobs are folded together on purpose — a pool's lifetime is when the
-- indexer observed it at all, not when one particular job ran.
life AS (
  SELECT l.target_address AS l_pool,
         min(l.snapshot_date) AS first_published,
         max(l.snapshot_date) AS last_published,
         uniqExact(l.snapshot_date) AS days_published
  FROM @pub AS l
  WHERE l.target_kind = 'pool' AND l.chain_id = @chain
  GROUP BY l_pool
)
