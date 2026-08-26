-- Single-chain as-of from census_publications — see the per-chain twin for
-- why the balances view must never be aggregated for date resolution, and why
-- every consumer also needs `t.snapshot_date IN (SELECT as_of FROM asof)`.
WITH asof AS (
  SELECT max(snapshot_date) AS as_of
  FROM @pub
  WHERE job_name = '@job' AND target_kind = 'token' AND chain_id = @chain
)
