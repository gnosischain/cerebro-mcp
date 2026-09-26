-- One row per (chain, wallet) at the as-of — every labelled census wallet on
-- every served chain (an empty wallet is a row with zeros, not a missing row),
-- plus any wallet the data holds that the label list lacks (FULL JOIN), so a new
-- census wallet can never silently vanish from the table. Such a wallet takes
-- its chain's as-of from its own rows: every row carries a date, never NULL.
WITH @pipeline,
per_wallet AS (
  SELECT c.x_chain AS bw_chain, c.x_wallet AS bw_wallet,
         uniqExactIf(c.x_token, c.x_visible) AS bw_tokens,
         countIf(c.x_class = 'priced') AS bw_priced,
         countIf(c.x_class IN ('listed', 'unverified')) AS bw_unpriced,
         countIf(NOT c.x_visible) AS bw_hidden,
         ifNull(sumIf(c.x_units, c.x_is_gno), 0) AS bw_gno,
         ifNull(sumIf(c.x_value, c.x_class = 'priced'), 0) AS bw_nav,
         any(c.x_as_of) AS bw_as_of
  FROM classified AS c
  GROUP BY bw_chain, bw_wallet
),
chain_asof AS (
  SELECT pk_chain AS ca_chain, any(pk_as_of) AS ca_as_of FROM picked GROUP BY ca_chain
),
spine AS (
  SELECT ca_chain AS sw_chain, arrayJoin({label_addr:Array(String)}) AS sw_wallet,
         ca_as_of AS sw_as_of
  FROM chain_asof
)
SELECT if(w.bw_chain != 0, w.bw_chain, s.sw_chain) AS chain_id,
       if(w.bw_wallet != '', w.bw_wallet, s.sw_wallet) AS wallet_address,
       transform(wallet_address, {label_addr:Array(String)}, {label_name:Array(String)}, '')
         AS wallet_label,
       {label_source:String} AS label_source,
       has({ltd:Array(String)}, wallet_address) AS is_ltd,
       w.bw_tokens AS tokens_held, w.bw_priced AS priced_positions,
       w.bw_unpriced AS unpriced_positions, w.bw_hidden AS hidden_positions,
       w.bw_gno AS gno_units, w.bw_nav AS nav_usd,
       toString(if(s.sw_chain != 0, s.sw_as_of, w.bw_as_of)) AS as_of
FROM spine AS s
FULL OUTER JOIN per_wallet AS w ON w.bw_chain = s.sw_chain AND w.bw_wallet = s.sw_wallet
ORDER BY nav_usd DESC, chain_id, wallet_address
