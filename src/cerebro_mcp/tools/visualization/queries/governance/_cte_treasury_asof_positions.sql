-- Two-step served read, step 2: the balances of exactly the picked attempts.
--
-- token_balances is a raw ReplacingMergeTree keyed (chain_id, job_name,
-- token_address, snapshot_date, attempt_id, holder_address) and holds billions of
-- rows since the holder census landed, so FINAL is forbidden (memory) and the
-- canonical view is too slow for history (its eligibility views dominate). The
-- read is bounded three ways, all mandatory: the job pin, a constant date bound
-- (partition prune) and the 4-tuple IN on the served attempt (primary-key prune).
-- argMax over insert_version per full key is the FINAL-equivalent dedup; the
-- attempt pin is what FINAL alone could never give (a raw table mixes attempts).
apos AS (
  SELECT b.chain_id AS ps_chain, b.token_address AS ps_token,
         b.holder_address AS ps_wallet,
         argMax(b.balance_raw, b.insert_version) AS ps_raw
  FROM @balances AS b
  WHERE b.job_name = '@job'
    AND b.snapshot_date >= today() - @window_days
    AND (b.chain_id, b.token_address, b.snapshot_date, b.attempt_id)
        IN (SELECT pk_chain, pk_token, pk_date, pk_attempt FROM picked)
  GROUP BY ps_chain, ps_token, ps_wallet
  HAVING ps_raw != 0
)
