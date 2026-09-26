-- Keep only the publication rows of ONE pool that v_publications_current
-- SERVES: the attempt each (job, day) resolved to. census_publications also holds
-- every re-census attempt and any publication that failed eligibility, and those
-- rows describe numbers no v_pool_* view returns. Lesson: published-is-not-served.
--
-- Bounded by the pool — the primary-key prefix the ledger is sorted by — and by
-- @served_bound, so the served view reads at most one pool's calendar (~1,100
-- pool-days, 0.6s for a pool published since 2023-09, measured 2026-09-26).
-- Unbounded, that view OOMs.
--
-- A scalar ARRAY, not `IN (SELECT ...)`: identical scalar subqueries run once per
-- query, but an IN set is rebuilt in every subquery context — and the CTEs that
-- carry this predicate are inlined per reference (the heatmap's days_all is read
-- four times), which took the heatmap from 0.4s to 3.0s. A pool's served calendar
-- is a few thousand tuples, well inside a constant.
has((SELECT groupArray((v.job_name, v.snapshot_date, v.attempt_id)) FROM @served AS v
     WHERE v.job_name IN (@jobs) AND v.target_kind = 'pool' AND v.chain_id = @chain
       AND v.target_address = {pool:String} AND @served_bound),
    (@alias.job_name, @alias.snapshot_date, @alias.attempt_id))
