-- Snapshot dates resolve from census_publications, NEVER by aggregating a
-- v_pool_* view. The views INNER JOIN publications, so a date exists in a view
-- iff it was published, and publications is small and narrow (~6M rows, ~0.1s
-- per aggregate) while the balance/state views sit on tables another writer
-- keeps growing.
--
-- Every consumer of this CTE MUST also carry the pruning predicate
--   <alias>.snapshot_date IN (SELECT as_of FROM asof)
-- beside its join. A JOIN key never prunes a ClickHouse scan; an uncorrelated
-- IN subquery folds to a constant set at plan time and prunes partitions and
-- the primary key. Lesson: fat-view-join-never-prunes.
WITH asof AS (
-- Consumers project the resolved date as toString((SELECT as_of FROM asof)).
-- That is not a style choice: with an unbounded predicate ClickHouse folds the
-- whole aggregate to a constant and the driver hands back the raw day number,
-- so the date reaches the UI as 20712. toDate(), CAST and materialize() all
-- still return the integer; only toString() survives the fold. Measured.
  SELECT toDate(max(snapshot_date)) AS as_of
  FROM @pub
  WHERE job_name = '@job' AND target_kind = 'pool' AND chain_id = @chain
    AND @asof_bound
)
