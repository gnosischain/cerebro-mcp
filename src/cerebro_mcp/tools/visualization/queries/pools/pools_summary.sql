@asof_cte,
@reserves_asof_cte,
@cfg_cte,
@st_cte,
@probe_cte,
@res_cte,
anc AS (
  SELECT max(a.block_number) AS anchor_block,
         max(a.block_timestamp) AS anchor_timestamp
  FROM @db.@anchors_view AS a
  WHERE a.chain_id = @chain AND a.snapshot_date IN (SELECT as_of FROM asof)
)
SELECT
  toString((SELECT as_of FROM asof)) AS as_of,
  any(anc.anchor_block) AS anchor_block,
  any(anc.anchor_timestamp) AS anchor_timestamp,
  count() AS pools_configured,
  countIf(cfg.pool_family = 'cl') AS pools_configured_cl,
  countIf(cfg.pool_family = 'reserves_only') AS pools_configured_reserves_only,
  countIf(st.st_pool != '') AS pools_published_cl,
  countIf(st.liquidity > 0) AS pools_live_cl,
  countIf(pr.ticks_probed) AS pools_probed,
  countIf(st.liquidity > 0 AND NOT pr.ticks_probed) AS pools_live_unprobed,
  toString((SELECT max(ras_of) FROM rasof)) AS reserves_as_of,
  uniqExactIf(rs.r_pool, rs.r_pool != '') AS pools_with_reserves,
  uniqExactIf(rs.r_pool, rs.r_any_positive AND cfg.pool_family = 'reserves_only')
    AS pools_live_reserves_only
FROM cfg
CROSS JOIN anc
LEFT JOIN st ON st.st_pool = cfg.pool_address
LEFT JOIN probe AS pr ON pr.p_pool = cfg.pool_address
LEFT JOIN res AS rs ON rs.r_pool = cfg.pool_address
ORDER BY as_of
