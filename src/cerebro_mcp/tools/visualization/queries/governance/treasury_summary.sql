-- One row per treasury chain at its as-of: served-snapshot status, class counts
-- (spam and retired mirrors counted, never shown in totals), GNO units and the
-- hub-priced ERC-20 holdings value, each with an ex-Ltd companion so the UI can
-- toggle Gnosis Ltd client-side. A chain with nothing served in the window keeps
-- a row with status no_served_snapshot instead of vanishing.
WITH @pipeline,
per_chain AS (
  SELECT c.x_chain AS pc_chain, any(c.x_as_of) AS pc_as_of, any(c.x_status) AS pc_status,
         any(c.x_block) AS pc_block, any(c.x_published) AS pc_published,
         any(c.x_served) AS pc_served,
         uniqExactIf(c.x_token, c.x_date < c.x_as_of) AS pc_carried,
         max(c.x_universe) AS pc_universe,
         uniqExactIf(c.x_wallet, c.x_visible) AS pc_wallets_active,
         uniqExactIf(c.x_token, c.x_visible) AS pc_tokens,
         countIf(c.x_visible) AS pc_positions,
         uniqExactIf(c.x_token, c.x_class = 'priced') AS pc_priced,
         uniqExactIf(c.x_token, c.x_class = 'listed') AS pc_listed,
         uniqExactIf(c.x_token, c.x_class = 'unverified') AS pc_unverified,
         uniqExactIf(c.x_token, c.x_class = 'spam') AS pc_spam,
         uniqExactIf(c.x_token, c.x_class = 'retired_mirror') AS pc_retired,
         ifNull(sumIf(c.x_units, c.x_is_gno), 0) AS pc_gno,
         ifNull(sumIf(c.x_units, c.x_is_gno AND NOT c.x_is_ltd), 0) AS pc_gno_ex,
         ifNull(sumIf(c.x_value, c.x_class = 'priced'), 0) AS pc_nav,
         ifNull(sumIf(c.x_value, c.x_class = 'priced' AND NOT c.x_is_ltd), 0) AS pc_nav_ex,
         minIf(c.x_price_day, c.x_class = 'priced') AS pc_oldest_price
  FROM classified AS c
  GROUP BY pc_chain
)
SELECT sp.sp_chain AS chain_id,
       if(pc.pc_chain = 0, NULL, toString(pc.pc_as_of)) AS as_of,
       if(pc.pc_chain = 0, 'no_served_snapshot', pc.pc_status) AS as_of_status,
       if(pc.pc_chain = 0, NULL, pc.pc_block) AS anchor_block,
       pc.pc_published AS published_tokens, pc.pc_served AS served_tokens,
       pc.pc_carried AS carried_tokens, pc.pc_universe AS wallets_tracked,
       pc.pc_wallets_active AS wallets_active, pc.pc_tokens AS tokens_held,
       pc.pc_positions AS positions, pc.pc_priced AS priced_tokens,
       pc.pc_listed AS listed_tokens, pc.pc_unverified AS unverified_tokens,
       pc.pc_spam AS hidden_spam_tokens, pc.pc_retired AS hidden_retired_tokens,
       if(pc.pc_chain = 0, NULL, pc.pc_gno) AS gno_units,
       if(pc.pc_chain = 0, NULL, pc.pc_gno_ex) AS gno_units_ex_ltd,
       if(pc.pc_chain = 0, NULL, pc.pc_nav) AS nav_usd,
       if(pc.pc_chain = 0, NULL, pc.pc_nav_ex) AS nav_usd_ex_ltd,
       if(pc.pc_oldest_price IS NULL, NULL, toString(pc.pc_oldest_price)) AS oldest_price_date,
       (SELECT toString(max(date)) FROM @hub_table) AS hub_latest_date
FROM (SELECT arrayJoin(@chain_ids) AS sp_chain) AS sp
LEFT JOIN per_chain AS pc ON pc.pc_chain = sp.sp_chain
ORDER BY chain_id
