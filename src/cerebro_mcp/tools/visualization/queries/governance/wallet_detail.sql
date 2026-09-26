-- One wallet on one chain at the as-of. Classification windows ran over ALL the
-- chain's wallets before this filter, so spam here is the same spam everywhere.
-- A tracked wallet holding nothing still returns its row (chain as-of, zeros).
WITH @pipeline,
wsum AS (
  SELECT c.x_chain AS wd_chain,
         uniqExactIf(c.x_token, c.x_visible) AS wd_tokens,
         countIf(c.x_class = 'priced') AS wd_priced,
         countIf(c.x_class IN ('listed', 'unverified')) AS wd_unpriced,
         countIf(NOT c.x_visible) AS wd_hidden,
         ifNull(sumIf(c.x_units, c.x_is_gno), 0) AS wd_gno,
         ifNull(sumIf(c.x_value, c.x_class = 'priced'), 0) AS wd_nav
  FROM classified AS c
  WHERE c.x_wallet = {addr:String}
  GROUP BY wd_chain
),
chain_asof AS (
  SELECT pk_chain AS ca_chain, any(pk_as_of) AS ca_as_of, any(pk_status) AS ca_status,
         maxIf(pk_block, pk_date = pk_as_of) AS ca_block
  FROM picked GROUP BY ca_chain
)
SELECT ca.ca_chain AS chain_id, {addr:String} AS wallet_address,
       transform({addr:String}, {label_addr:Array(String)}, {label_name:Array(String)}, '')
         AS wallet_label,
       {label_source:String} AS label_source,
       has({ltd:Array(String)}, {addr:String}) AS is_ltd,
       toString(ca.ca_as_of) AS as_of, ca.ca_status AS as_of_status,
       ca.ca_block AS anchor_block,
       ws.wd_tokens AS tokens_held, ws.wd_priced AS priced_positions,
       ws.wd_unpriced AS unpriced_positions, ws.wd_hidden AS hidden_positions,
       ws.wd_gno AS gno_units, ws.wd_nav AS nav_usd
FROM chain_asof AS ca
LEFT JOIN wsum AS ws ON ws.wd_chain = ca.ca_chain
ORDER BY chain_id
