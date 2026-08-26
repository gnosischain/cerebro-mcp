-- Per-chain as-of dates resolve from census_publications (millions of small
-- rows), NEVER by aggregating the balances view: since the holder census
-- landed, token_balances holds billions of rows and the view FINAL-merges all
-- of it — a max(snapshot_date) over the view OOMs at the server-wide cap.
-- Publications are authoritative for which dates exist: the view INNER JOINs
-- them, so a date is in the view iff it was published. Every scan that joins
-- this CTE must ALSO carry the pruning predicate
--   t.snapshot_date IN (SELECT as_of FROM asof)
-- — the JOIN alone never prunes the view scan; the uncorrelated IN folds to a
-- constant set at plan time and prunes partitions + primary key (measured:
-- OOM -> ~2s on 2026-08-26 data).
WITH asof AS (
  SELECT chain_id, max(snapshot_date) AS as_of
  FROM @pub
  WHERE job_name = '@job' AND target_kind = 'token'
  GROUP BY chain_id
)
