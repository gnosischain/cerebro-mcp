-- The heatmap's date bound: the stride-sampled day list, not the as-of. Same
-- constant-folding IN as the as-of prune, against the sampled set that caps
-- the grid at its configured number of dates.
@alias.snapshot_date IN (SELECT snapshot_date FROM days)
