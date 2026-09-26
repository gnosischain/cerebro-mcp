-- Per-day tick-probe flag for ONE pool, so a history series can grey out the
-- days the indexer did not read the pool's ticks instead of drawing them the
-- same as days it did. Publications only, bounded by the series' own window —
-- at most one pool's calendar of narrow rows.
--
-- One row per day, from that day's SERVED attempt: the history datasets LEFT JOIN
-- this by date, and a re-census's second raw publication drew the day twice.
-- Lesson: published-is-not-served.
probe_days AS (
  SELECT p.snapshot_date AS pd_date,
         NOT has(p.checks_passed, '@check') AS ticks_probed
  FROM @pub AS p
  WHERE p.job_name = '@job' AND p.target_kind = 'pool' AND p.chain_id = @chain
    AND p.target_address = {pool:String} AND @window_pub
    AND @served_pred
  ORDER BY pd_date, p.published_at DESC LIMIT 1 BY pd_date
)
