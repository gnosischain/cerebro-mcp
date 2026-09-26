-- The provenance panel: which attempt produced this pool-day, which finalized
-- block it was pinned to, and exactly which integrity checks passed. This is
-- where "why is the tick table empty" gets its answer, so the raw checks array
-- is emitted rather than a summarised verdict.
--
-- Only the SERVED publication of each job: the attempt v_publications_current
-- selected, which is the one the state and balances views' numbers come from. A
-- re-census leaves several raw rows for the same pool-day, and listing them all
-- made the panel show two provenances for one set of numbers.
-- Lesson: published-is-not-served.
@asof_cte,
@reserves_asof_cte
SELECT
  toString(p.snapshot_date) AS snapshot_date,
  p.job_name AS job_name,
  p.anchor_block AS anchor_block,
  p.anchor_hash AS anchor_hash,
  a.block_timestamp AS block_timestamp,
  toString(p.publication_id) AS publication_id,
  toString(p.attempt_id) AS attempt_id,
  p.published_at AS published_at,
  p.integrity_mode AS integrity_mode,
  p.block_reference_kind AS block_reference_kind,
  p.executor_kind AS executor_kind,
  p.observations_total AS observations_total,
  p.checks_passed AS checks_passed,
  NOT has(p.checks_passed, '@check') AS ticks_probed,
  has(p.checks_passed, 'cl_liquidity_net_sum_zero') AS net_sum_zero_passed,
  has(p.checks_passed, 'cl_active_liquidity_reconciles') AS reconciles_passed
FROM @pub AS p
LEFT JOIN @db.@anchors_view AS a
       ON a.chain_id = p.chain_id AND a.snapshot_date = p.snapshot_date
WHERE p.target_kind = 'pool' AND p.chain_id = @chain
  AND p.target_address = {pool:String}
  AND ((p.job_name = '@cl_job' AND p.snapshot_date = (SELECT as_of FROM asof))
    OR (p.job_name = '@reserves_job' AND p.snapshot_date = (SELECT ras_of FROM rasof)))
  AND @served_pred
ORDER BY job_name, snapshot_date
