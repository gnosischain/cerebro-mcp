-- Per chain-month completeness of the history, so a month is DISCLOSED rather
-- than silently dipped or dropped. Published = distinct tokens with any raw
-- publication in the month's candidate days; served = those with a served day
-- (possibly carried from an earlier candidate day, counted); unserved registry
-- tokens are named with their TRUSTED registry symbols. A calendar spine keeps
-- months with no publication at all as 'unpublished' rows.
--   complete    — every published token served (within the ratio), no registry token missing
--   partial     — values drawn; some tokens missing (named when in the registry)
--   gap         — nothing served that month: never drawn as a zero
--   unpublished — no census ran that month
WITH @candidates,
@served,
@registry,
raw_m AS (
  SELECT chain_id AS rm_chain, toStartOfMonth(snapshot_date) AS rm_bucket,
         target_address AS rm_token, max(snapshot_date) AS rm_last
  FROM @pub
  WHERE job_name = '@job' AND target_kind = 'token' AND @chain_pred
    AND snapshot_date IN (SELECT c_date FROM cand)
    AND (chain_id, snapshot_date) IN (SELECT c_chain, c_date FROM cand)
  GROUP BY rm_chain, rm_bucket, rm_token
),
joined AS (
  SELECT r.rm_chain AS j_chain, r.rm_bucket AS j_bucket, r.rm_token AS j_token,
         r.rm_last AS j_last, s.sm_token != '' AS j_served, s.sm_date AS j_date,
         max(s.sm_date) OVER (PARTITION BY r.rm_chain, r.rm_bucket) AS j_bucket_date
  FROM raw_m AS r
  LEFT JOIN served_m AS s
         ON s.sm_chain = r.rm_chain AND s.sm_bucket = r.rm_bucket AND s.sm_token = r.rm_token
),
roled AS (
  SELECT j.*,
         if(j.j_last < g.reg_to, g.reg_role, '') AS j_role,
         if(j.j_last < g.reg_to, g.reg_symbol, '') AS j_symbol
  FROM joined AS j
  ASOF LEFT JOIN reg AS g
    ON g.reg_chain = j.j_chain AND g.reg_token = j.j_token AND j.j_last >= g.reg_from
),
per_bucket AS (
  SELECT j_chain AS pb_chain, j_bucket AS pb_bucket, max(j_last) AS pb_raw_end,
         max(j_bucket_date) AS pb_bucket_date,
         uniqExact(j_token) AS pb_published,
         uniqExactIf(j_token, j_served) AS pb_served,
         uniqExactIf(j_token, j_served AND j_date < j_bucket_date) AS pb_carried,
         uniqExactIf(j_token, NOT j_served) AS pb_unserved,
         uniqExactIf(j_token, NOT j_served AND j_role IN ('priced', 'listed')) AS pb_unserved_reg,
         arraySort(groupUniqArrayIf(j_symbol, NOT j_served AND j_role IN ('priced', 'listed')))
           AS pb_unserved_syms
  FROM roled
  GROUP BY pb_chain, pb_bucket
),
spine AS (
  SELECT sb_chain, addMonths(sb_min, toInt32(n)) AS sb_bucket
  FROM (
    SELECT c_chain AS sb_chain, toStartOfMonth(min(c_date)) AS sb_min,
           toStartOfMonth(max(c_date)) AS sb_max
    FROM cand GROUP BY sb_chain
  )
  ARRAY JOIN range(toUInt32(dateDiff('month', sb_min, sb_max) + 1)) AS n
)
SELECT s.sb_chain AS chain_id, toString(s.sb_bucket) AS bucket,
       if(p.pb_chain = 0, NULL, toString(p.pb_raw_end)) AS raw_month_end,
       if(p.pb_served = 0, NULL, toString(p.pb_bucket_date)) AS bucket_date,
       p.pb_published AS published_tokens, p.pb_served AS served_tokens,
       p.pb_carried AS carried_tokens, p.pb_unserved AS unserved_tokens,
       p.pb_unserved_reg AS unserved_registry_tokens,
       p.pb_unserved_syms AS unserved_registry_symbols,
       multiIf(p.pb_published = 0, 'unpublished', p.pb_served = 0, 'gap',
               p.pb_unserved_reg > 0 OR p.pb_served < @ratio * p.pb_published, 'partial',
               'complete') AS status
FROM spine AS s
LEFT JOIN per_bucket AS p ON p.pb_chain = s.sb_chain AND p.pb_bucket = s.sb_bucket
ORDER BY chain_id, bucket
