-- Either resolved date: the CL as-of or the reserves as-of. Bounds a read that
-- needs a pool's facts at both snapshots and nothing else. Scalar subqueries, so
-- they fold to constants at plan time and run once per query (_pred_asof_prune).
(@alias.snapshot_date = (SELECT as_of FROM asof)
 OR @alias.snapshot_date = (SELECT ras_of FROM rasof))
