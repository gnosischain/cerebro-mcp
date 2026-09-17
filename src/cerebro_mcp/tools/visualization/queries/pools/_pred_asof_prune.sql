-- The prune every scan of a dated view must carry beside its as-of join. A
-- JOIN key never prunes a ClickHouse scan; this uncorrelated IN folds to a
-- constant set at plan time and prunes partitions and the primary key.
-- Lesson: fat-view-join-never-prunes.
@alias.snapshot_date IN (SELECT as_of FROM asof)
