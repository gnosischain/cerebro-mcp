-- Monthly balance series for the most interesting tokens still held.
--
-- COST HISTORY — this timed out in production (code 159, 20 s budget exceeded).
-- One bare scan of the view costs ~2.7 s (measured), because @src resolves
-- ReplacingMergeTree dedup internally and every scan pays it. The old shape cost
-- SIX scans:
--
--   * `per_bucket` was referenced TWICE (in `picked` and in the final SELECT), and
--     ClickHouse inlines a CTE per reference — so its scan AND the `months` scan
--     nested inside it both doubled;
--   * month-ends were resolved with `INNER JOIN months ON snapshot_date = month_end`,
--     a filter expressed as a join: chain 100 publishes daily, so ~29 of every 30
--     rows were read and thrown away, twice.
--
-- Now THREE effective scans (`months`, `per_bucket`, `held_now`):
--   * the month-end filter is a tuple `IN` inside the raw scan, which also removes
--     the join and lets `bucket` come straight from the date;
--   * `per_bucket` is referenced ONCE — the ranking that needed a second pass is a
--     window function instead.
--
-- Measured 2026-07-30, production `exact_count` shape: before 13.9-15.1 s (it was
-- failing the 20 s interactive budget under load), after 8.5-10.7 s.
--
-- EQUIVALENCE was proven per changed piece, each with BOTH forms inside ONE query.
-- That framing is not pedantry: `v_treasury_balances` is written daily and was being
-- written during this work, so the same query run twice minutes apart returned 1118
-- then 1119 rows with different checksums. Comparing two SEPARATE queries against
-- this view measures the write, not the rewrite.
--   * per_bucket, JOIN+m.bucket vs tuple-IN+toStartOfMonth:
--       11566 rows and checksum 12306172241941041485 on both.
--   * changes, GROUP BY vs window: 309 tokens, 0 disagreements,
--       checksum 16113356343225184785 on both.
--   * selection, `LIMIT 24 BY` vs `dense_rank() <= 24`: 0 divergent tokens.
--     (dense_rank ranks DISTINCT key values, and the key ends in token_address, so
--      one rank == one token no matter how many buckets that token has.)
--
-- Do NOT drop the `job_name` pin: it is what keeps this off the 185M-row
-- full_holders universe, and it is separately test-enforced.
WITH @months_cte,
@asof_cte_body,
per_bucket AS (
  SELECT t.chain_id AS chain_id, t.token_address AS token_address,
         -- `bucket` from the date itself, not from a joined `months.bucket`. Safe by
         -- construction: `months` groups BY toStartOfMonth(snapshot_date) and takes
         -- max() within the group, so toStartOfMonth(month_end) == bucket always.
         toStartOfMonth(t.snapshot_date) AS bucket,
         anyHeavy(t.symbol) AS symbol, anyHeavy(t.decimals) AS decimals,
         anyHeavy(t.metadata_status) AS metadata_status,
         sum(t.balance_raw) AS balance_raw_sum,
         sum(t.balance_units) AS balance_units,
         uniqExact(t.wallet_address) AS wallets_holding
  FROM @src AS t
  WHERE t.job_name = '@job' AND @chain_sql AND @asset_sql AND @ltd_sql
    AND t.balance_raw != 0
    -- The TUPLE keeps the pairing per chain. A bare `snapshot_date IN (...)` would
    -- admit one chain's month-end as another chain's mid-month date and sum two
    -- dates into one bucket — double-counting that no test would catch.
    AND (t.chain_id, t.snapshot_date) IN (SELECT chain_id, month_end FROM months)
  GROUP BY chain_id, token_address, bucket
),
held_now AS (
  -- Deliberately still its own scan against `asof`, NOT derived from the last
  -- bucket. "Held now" means nonzero at the as-of DATE; a token can hold a
  -- balance earlier in the final month and none on the as-of date, and the
  -- cheaper month-grained version admitted 35 extra rows.
  SELECT t.chain_id AS h_chain, t.token_address AS h_token
  FROM @src AS t
  INNER JOIN asof AS a ON t.chain_id = a.chain_id AND t.snapshot_date = a.as_of
  WHERE t.job_name = '@job' AND t.snapshot_date IN (SELECT as_of FROM asof)
    AND @chain_sql AND @asset_sql AND @ltd_sql
    AND t.balance_raw != 0
  GROUP BY h_chain, h_token
),
windowed AS (
  -- `changes` as a WINDOW, so `per_bucket` is read once. It used to be a second
  -- GROUP BY pass over `per_bucket`, which is what doubled the scan.
  SELECT b.chain_id AS chain_id, b.token_address AS token_address, b.bucket AS bucket,
         b.symbol AS symbol, b.decimals AS decimals, b.metadata_status AS metadata_status,
         b.balance_raw_sum AS balance_raw_sum, b.balance_units AS balance_units,
         b.wallets_holding AS wallets_holding,
         uniqExact(b.balance_raw_sum) OVER (PARTITION BY b.chain_id, b.token_address) AS changes
  FROM per_bucket AS b
  INNER JOIN held_now AS h ON h.h_chain = b.chain_id AND h.h_token = b.token_address
),
ranked AS (
  -- Replaces `LIMIT @history_tokens BY p_chain`. The ORDER BY must stay TOTAL —
  -- `token_address` last is not decoration. Most candidates tie at changes=1 and the
  -- cut lands inside a multi-way tie at the boundary, so without a unique last key
  -- the selected set is whatever the plan happened to produce, and it moves when the
  -- plan does. A window cannot be nested inside another window's ORDER BY (code 10,
  -- NOT_FOUND_COLUMN_IN_BLOCK), hence the separate `windowed` layer above.
  SELECT *,
         dense_rank() OVER (
           PARTITION BY chain_id
           ORDER BY (token_address = @gno_picked_sql) DESC,
                    changes DESC, token_address
         ) AS rnk
  FROM windowed
)
SELECT chain_id, bucket, token_address, symbol, decimals, metadata_status,
       balance_units,
       toString(balance_raw_sum) AS balance_total_raw,
       wallets_holding
FROM ranked
WHERE rnk <= @history_tokens
ORDER BY bucket, chain_id, token_address
