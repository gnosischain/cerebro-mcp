-- The pool universe. config_registry is a ReplacingMergeTree whose rows are
-- re-registered whenever a job config changes, so FINAL is MANDATORY here —
-- it is the only relation on this plane that takes it (the v_* views resolve
-- dedup internally and must never be FINAL'd).
--
-- The reserves job is read rather than the CL job because its target set is a
-- strict superset: every concentrated-liquidity pool is also probed for token
-- balances, and Balancer v2/v3 pools are probed for balances only. pool_family
-- is derived from pool_class, which is what actually decides whether tick-level
-- data can exist for a pool.
--
-- Assets arrive as a JSON array of objects and are address-ascending, so
-- assets[1]/assets[2] are token0/token1 for the two-asset CL pools. A Balancer
-- pool holds up to 8, so n_assets is emitted and the pair columns are read only
-- where n_assets = 2.
cfg AS (
  SELECT
    c.target_address AS pool_address,
    JSONExtractString(c.canonical_config_json, 'target', 'pool_class') AS pool_class,
    if(pool_class IN (@cl_classes), 'cl', 'reserves_only') AS pool_family,
    JSONExtractString(c.canonical_config_json, 'target', 'name') AS pool_name,
    JSONExtractString(c.canonical_config_json, 'target', 'pool_id') AS pool_id,
    JSONExtractUInt(c.canonical_config_json, 'target', 'deployment_block') AS deployment_block,
    arrayMap(x -> JSONExtractString(x, 'token'),
             JSONExtractArrayRaw(c.canonical_config_json, 'target', 'assets')) AS assets,
    length(assets) AS n_assets,
    assets[1] AS token0,
    if(length(assets) > 1, assets[2], '') AS token1
  FROM @db.config_registry AS c FINAL
  WHERE c.chain_id = @chain AND c.job_name = '@job' AND c.target_kind = 'pool'
    AND c.enabled = 1 AND @pool_sql
)
