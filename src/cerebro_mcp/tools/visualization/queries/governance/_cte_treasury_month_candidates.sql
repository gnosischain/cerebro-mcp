-- Candidate days per (chain, month): the last few RAW-published days of each month.
-- Raw publications are cheap and say which days a census RAN; they are only the
-- candidate set. Which of those days is actually served is decided per token by
-- the served CTE — a raw day can be wholly unserved (ineligible publications).
cand AS (
  SELECT c_chain, c_date FROM (
    SELECT chain_id AS c_chain, snapshot_date AS c_date
    FROM @pub
    WHERE job_name = '@job' AND target_kind = 'token' AND @chain_pred
    GROUP BY c_chain, c_date
    ORDER BY c_chain, c_date DESC
    LIMIT @candidate_days BY c_chain, toStartOfMonth(c_date)
  )
)
