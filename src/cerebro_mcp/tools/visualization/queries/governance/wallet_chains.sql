-- The same wallet address on every served treasury chain — drives the wallet
-- page's chain switcher ("also holds $X on Gnosis Chain").
WITH @pipeline,
wsum AS (
  SELECT c.x_chain AS wc_chain,
         uniqExactIf(c.x_token, c.x_visible) AS wc_tokens,
         ifNull(sumIf(c.x_value, c.x_class = 'priced'), 0) AS wc_nav
  FROM classified AS c
  WHERE c.x_wallet = {addr:String}
  GROUP BY wc_chain
),
chain_asof AS (
  SELECT pk_chain AS ca_chain, any(pk_as_of) AS ca_as_of FROM picked GROUP BY ca_chain
)
SELECT ca.ca_chain AS chain_id, {addr:String} AS wallet_address,
       toUInt8(has({label_addr:Array(String)}, {addr:String}) OR ws.wc_chain != 0) AS tracked,
       toUInt8(ws.wc_tokens > 0) AS has_positions,
       ws.wc_tokens AS tokens_held, ws.wc_nav AS nav_usd,
       toString(ca.ca_as_of) AS as_of
FROM chain_asof AS ca
LEFT JOIN wsum AS ws ON ws.wc_chain = ca.ca_chain
ORDER BY chain_id
