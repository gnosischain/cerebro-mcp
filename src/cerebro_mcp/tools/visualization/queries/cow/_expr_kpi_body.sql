-- The protocol-KPI projection list, WITHOUT a leading SELECT.
--
-- It has no SELECT on purpose: protocol_kpis.sql splices this into two arms (a
-- per-chain one and a rolled-up chain_id=0 one), each of which supplies its own
-- `SELECT <chain expr>,` prefix.
--
-- This used to be kpi_select.sql — a file that DID start with `SELECT ` — which
-- the caller then post-processed with `.replace("SELECT ", "", 1)`. That was
-- fragile in a way nothing would have caught: `replace(..., 1)` removes the FIRST
-- occurrence anywhere in the rendered text, so adding a comment header to the
-- file, or any earlier `SELECT ` inside a substituted fragment, would have
-- silently mangled the projection instead of stripping the prefix.
--
-- Volume valuation is counts-first: cow_db has NO historical price source
-- (native_prices is a live snapshot, auction_prices is patchy), so an approximate
-- native-denominated volume is attached ONLY for short relative windows and is a
-- typed NULL otherwise — never a fabricated figure. The caller decides which,
-- via the vol_expr and np_join fragments.

  uniq(@trade_key) AS fill_count,uniq(t.tx_hash) AS settlement_transactions,
         uniq(t.owner) AS unique_traders,
         uniq(tuple(least(t.sell_token,t.buy_token),greatest(t.sell_token,t.buy_token))) AS unique_pairs,
         @vol_expr,
         minOrNull(t.block_timestamp) AS indexed_from,
         maxOrNull(t.block_timestamp) AS indexed_to,
         max(t.observed_at) AS source_observed_at
  FROM cow_db.trades AS t
  INNER JOIN cp ON cp.chain_id=t.chain_id
@np_join  WHERE @kpi_where
