-- History window relative to the resolved as-of rather than to now(): the
-- indexer can be a day or more behind, and anchoring to wall-clock time would
-- silently shorten every series by the lag. The scalar subquery folds to a
-- constant at plan time, so this still prunes.
@alias.snapshot_date >= (SELECT as_of FROM asof) - toIntervalDay(@days)
