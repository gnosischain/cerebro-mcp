-- Per-chain month-end dates from census_publications — see
-- _cte_treasury_asof_per_chain for why the balances view must never be
-- aggregated for date resolution. Consumers joining this CTE must ALSO carry
-- `t.snapshot_date IN (SELECT month_end FROM months)` — the join never prunes
-- the view scan; the IN does.
--
-- BOUNDED to the latest @history_months month-ends PER CHAIN (per-chain, so
-- the stale chain keeps its own most recent history rather than being cut by
-- the current chain's calendar). The view scan costs ~0.4s per selected date
-- (measured 2026-08-26: 52 dates = 22s, over the 20s interactive budget;
-- 24/chain = ~10s). The bound is DISCLOSED in each history spec's basis; the
-- full-history restoration path is an upstream materialized treasury slice.
months AS (
  SELECT chain_id, bucket, month_end FROM (
    SELECT chain_id, toStartOfMonth(snapshot_date) AS bucket,
           max(snapshot_date) AS month_end
    FROM @pub
    WHERE job_name = '@job' AND target_kind = 'token'
    GROUP BY chain_id, bucket
    ORDER BY chain_id, bucket DESC
    LIMIT @history_months BY chain_id
  )
)
