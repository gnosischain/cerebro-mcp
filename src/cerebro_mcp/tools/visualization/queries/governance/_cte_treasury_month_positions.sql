-- Month-end balances of exactly the served attempts (two-step read, step 2) and
-- the month base in the shared x_ contract. Same bounds as the as-of read: job
-- pin, constant date prune (candidate days) and the 4-tuple attempt IN; argMax
-- over insert_version per full key replaces FINAL on this raw table.
-- x_wallets / x_active are per MONTH, over all wallets present in the read, so
-- the mass-airdrop rule describes that month. A wallet- or token-restricted read
-- only ever feeds priced registry rows, which the classifier never touches.
mpos AS (
  SELECT b.chain_id AS mp_chain, b.token_address AS mp_token,
         b.snapshot_date AS mp_date, b.holder_address AS mp_wallet,
         argMax(b.balance_raw, b.insert_version) AS mp_raw
  FROM @balances AS b
  WHERE b.job_name = '@job' AND @holder_pred
    AND b.snapshot_date IN (SELECT c_date FROM cand)
    AND (b.chain_id, b.token_address, b.snapshot_date, b.attempt_id)
        IN (SELECT sm_chain, sm_token, sm_date, sm_attempt FROM served_m)
  GROUP BY mp_chain, mp_token, mp_date, mp_wallet
  HAVING mp_raw != 0
),
mbase AS (
  SELECT m.mp_chain AS x_chain, m.mp_token AS x_token, m.mp_wallet AS x_wallet,
         m.mp_raw AS x_raw, m.mp_date AS x_date, toStartOfMonth(m.mp_date) AS x_bucket,
         count() OVER (PARTITION BY m.mp_chain, m.mp_token, toStartOfMonth(m.mp_date))
           AS x_wallets,
         uniqExact(m.mp_wallet) OVER (PARTITION BY m.mp_chain, toStartOfMonth(m.mp_date))
           AS x_active
  FROM mpos AS m
)
