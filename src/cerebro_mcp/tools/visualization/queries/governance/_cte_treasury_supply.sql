-- Reference totalSupply of each served attempt, from the publication row itself.
-- The census records it for every target, so supply is pinned to the SAME attempt
-- (hence the same anchor block) as the balances it denominates. Replaces the
-- published-scalars view, which aggregated all census jobs.
supply AS (
  SELECT chain_id AS su_chain, target_address AS su_token, attempt_id AS su_attempt,
         argMax(reference_supply_raw, published_at) AS su_supply
  FROM @pub
  WHERE job_name = '@job' AND target_kind = 'token' AND @chain_pred
    AND snapshot_date >= today() - @window_days
  GROUP BY su_chain, su_token, su_attempt
)
