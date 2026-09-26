-- Per-chain AS-OF resolution for the treasury plane — from SERVED publications.
--
-- A raw census_publications row is NOT a served snapshot. The balances the plane
-- serves are only those whose publication is eligible (config hash matches the
-- registry, canonical anchor, verified attempt, no errors) and conflict-free —
-- exactly v_publications_current. Resolving dates from raw publications admitted
-- 2026-07-28..08-15 on Ethereum (891 tokens/day published under an interim config
-- hash, 0 served) and emptied the July bucket. Lesson: published-is-not-served.
--
-- A day is COMPLETE when its served token count is at least the ratio times BOTH
-- the raw count that day (rejects eligibility failures) and the peak served count
-- in the window (rejects a census still running, where raw and served are both
-- partial). The as-of is the latest complete day; if none is complete in the
-- window the latest served day is used and flagged 'partial'. A token unserved on
-- the as-of day is CARRIED from its own latest served day within the carry bound,
-- and counted — never silently dropped, never invented.
--
-- The window is anchored on today(), which folds to a constant and prunes
-- partitions of every source it bounds.
raw_days AS (
  SELECT chain_id AS rd_chain, snapshot_date AS rd_date,
         uniqExact(target_address) AS rd_published
  FROM @pub
  WHERE job_name = '@job' AND target_kind = 'token' AND @chain_pred
    AND snapshot_date >= today() - @window_days
  GROUP BY rd_chain, rd_date
),
win AS (
  SELECT chain_id AS w_chain, target_address AS w_token, snapshot_date AS w_date,
         attempt_id AS w_attempt, anchor_block AS w_block, universe_size AS w_universe,
         uniqExact(target_address) OVER (PARTITION BY chain_id, snapshot_date) AS w_served
  FROM @served
  WHERE job_name = '@job' AND target_kind = 'token' AND @chain_pred
    AND snapshot_date >= today() - @window_days
),
flagged AS (
  SELECT w.*, ifNull(r.rd_published, 0) AS w_published,
         max(w.w_served) OVER (PARTITION BY w.w_chain) AS w_peak
  FROM win AS w
  LEFT JOIN raw_days AS r ON r.rd_chain = w.w_chain AND r.rd_date = w.w_date
),
resolved AS (
  SELECT *,
         max(if(w_served >= @ratio * greatest(w_published, w_peak), w_date, toDate(0)))
           OVER (PARTITION BY w_chain) AS w_complete,
         max(w_date) OVER (PARTITION BY w_chain) AS w_latest
  FROM flagged
),
anchored AS (
  SELECT *,
         if(w_complete > toDate(0), w_complete, w_latest) AS w_as_of,
         if(w_complete > toDate(0), 'complete', 'partial') AS w_status
  FROM resolved
),
picked AS (
  SELECT w_chain AS pk_chain, w_token AS pk_token,
         max(w_date) AS pk_date, argMax(w_attempt, w_date) AS pk_attempt,
         any(w_as_of) AS pk_as_of, any(w_status) AS pk_status,
         max(w_universe) AS pk_universe,
         maxIf(w_block, w_date = w_as_of) AS pk_block,
         maxIf(w_published, w_date = w_as_of) AS pk_published,
         maxIf(w_served, w_date = w_as_of) AS pk_served
  FROM anchored
  WHERE w_date <= w_as_of AND w_date > w_as_of - @max_carry_days
  GROUP BY pk_chain, pk_token
)
