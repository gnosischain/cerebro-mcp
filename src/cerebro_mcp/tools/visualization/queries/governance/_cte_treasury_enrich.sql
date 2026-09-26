-- Attach token metadata (on-chain symbol/name/decimals — untrusted text) and the
-- registry entry valid on each row's own date. Two layers so each join keeps a
-- simple left side. The registry join is ASOF on valid_from; the valid_to check
-- turns a row outside every window (e.g. before a token was reviewed) into ''.
-- An ASOF LEFT JOIN miss yields the right side's defaults (reg_to = 1970-01-01),
-- which the same check also maps to ''.
enriched_meta AS (
  SELECT q.*, md.symbol AS x_symbol, md.name AS x_name, md.decimals AS x_meta_dec,
         if(md.token_address = '', 'missing', md.resolution_status) AS x_meta_status
  FROM @base AS q
  LEFT JOIN @metadata AS md ON md.chain_id = q.x_chain AND md.token_address = q.x_token
),
enriched AS (
  SELECT e.*,
         if(e.x_date < r.reg_to, r.reg_role, '') AS x_role,
         if(e.x_date < r.reg_to, r.reg_psym, '') AS x_psym,
         if(e.x_date < r.reg_to, r.reg_basis, '') AS x_basis,
         if(e.x_date < r.reg_to, r.reg_symbol, '') AS x_reg_symbol,
         if(e.x_date < r.reg_to, r.reg_asset, '') AS x_reg_asset,
         if(e.x_date < r.reg_to, r.reg_class, '') AS x_reg_class,
         if(e.x_date < r.reg_to, toInt16(r.reg_dec), toInt16(-1)) AS x_reg_dec,
         has({ltd:Array(String)}, e.x_wallet) AS x_is_ltd
  FROM enriched_meta AS e
  ASOF LEFT JOIN reg AS r
    ON r.reg_chain = e.x_chain AND r.reg_token = e.x_token AND e.x_date >= r.reg_from
)
