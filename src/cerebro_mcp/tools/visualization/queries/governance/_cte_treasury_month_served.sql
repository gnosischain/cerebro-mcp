-- Per (chain, token, month): the token's LATEST SERVED candidate day and its
-- attempt. A token unserved on the month's last day is carried from its own last
-- served day in the candidate window; the coverage dataset counts the carries.
-- The tuple IN keeps chains independent: a bare date IN would admit one chain's
-- month-end as another chain's mid-month day. The date-only IN beside it folds to
-- a constant set and prunes; the tuple IN does the exact filtering.
served_m AS (
  SELECT chain_id AS sm_chain, target_address AS sm_token,
         toStartOfMonth(snapshot_date) AS sm_bucket,
         max(snapshot_date) AS sm_date, argMax(attempt_id, snapshot_date) AS sm_attempt
  FROM @served
  WHERE job_name = '@job' AND target_kind = 'token' AND @token_pred
    AND snapshot_date IN (SELECT c_date FROM cand)
    AND (chain_id, snapshot_date) IN (SELECT c_chain, c_date FROM cand)
  GROUP BY sm_chain, sm_token, sm_bucket
)
