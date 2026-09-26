-- The CL as-of resolves from SERVED publications — never from raw
-- census_publications alone, and never by aggregating a v_pool_* view.
--
-- A census_publications row is what an attempt WROTE. What the v_pool_* views
-- SERVE is narrower: they INNER JOIN v_publications_current, which keeps only
-- eligible publications (registered config hash, canonical anchor, verified
-- attempt, no errors) with one signature per pool-day. So a day can be published
-- and not served, and max() over raw publications resolves a date the views have
-- nothing for — the governance treasury lost a month that way.
-- Lesson: published-is-not-served.
--
-- Raw publications still pick the CANDIDATES: the latest @candidate_days days the
-- job published on or before the bound. That scan is cheap (narrow ledger) and
-- robust to a hole of any length. The served view is then read for exactly those
-- days: unbounded it OOMs (2 GiB over the two pool jobs' history, measured
-- 2026-09-26), and the uncorrelated IN folds to a constant set that prunes it.
--
-- The as-of is the latest COMPLETE candidate (see _expr_served_day_complete):
-- at least @ratio of the pools published that day AND of the most any of the
-- previous @peak_days candidates served. That rejects an eligibility failure, a
-- run still in progress (the CL job publishes over 1-4.5 hours every day, and the
-- raw max picked the half-written day for all of that time) and a run that
-- stopped part-way (2026-08-23 stopped at 1,082 of 2,519 pools). With no complete
-- candidate it falls back to the latest day that served anything.
--
-- Every consumer of this CTE MUST also carry the pruning predicate
--   <alias>.snapshot_date = (SELECT as_of FROM asof)
-- beside its join. A JOIN key never prunes a ClickHouse scan; an uncorrelated
-- scalar subquery folds to a constant at plan time and prunes partitions and the
-- primary key. Lesson: fat-view-join-never-prunes. Scalar, never IN: identical
-- scalar subqueries run once per query, while every IN context re-runs this whole
-- chain, served read included (see _pred_asof_prune).
--
-- Consumers project the resolved date as toString((SELECT as_of FROM asof)).
-- With an unbounded predicate ClickHouse folds the aggregate to a constant and the
-- driver hands back the raw day number (20712); toDate(), CAST and materialize()
-- all still return the integer; only toString() survives the fold. Measured.
WITH asof_raw AS (
  SELECT snapshot_date AS ar_date, uniqExact(target_address) AS ar_published
  FROM @pub
  WHERE job_name = '@job' AND target_kind = 'pool' AND chain_id = @chain
    AND @asof_bound
  GROUP BY ar_date ORDER BY ar_date DESC LIMIT @candidate_days
),
asof_srv AS (
  SELECT snapshot_date AS as_date, count() AS as_served
  FROM @served
  WHERE job_name = '@job' AND target_kind = 'pool' AND chain_id = @chain
    AND snapshot_date IN (SELECT ar_date FROM asof_raw)
  GROUP BY as_date
),
asof_day AS (
  SELECT r.ar_date AS ad_date, s.as_served AS ad_served, @complete AS ad_complete
  FROM asof_raw AS r LEFT JOIN asof_srv AS s ON s.as_date = r.ar_date
),
asof AS (
  SELECT if(countIf(ad_complete) > 0, maxIf(ad_date, ad_complete),
            maxIf(ad_date, ad_served > 0)) AS as_of
  FROM asof_day
)
