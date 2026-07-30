-- Attach the live native-price snapshot to a trade row, by (chain, sell_token).
--
-- LEFT, never INNER: an unpriced token must keep its trade in the counts with a
-- NULL price rather than drop the row. The caller pairs this with
-- `sumIf(..., np.token!='')` so unpriced rows contribute 0 to the value sum while
-- still being counted.
--
-- Only emitted for short relative windows: cow_db has NO historical price source
-- (native_prices is a live snapshot), so valuing a long window at today's price
-- would be a fabricated number. Longer windows get a typed NULL instead.
--
-- THE TRAILING BLANK LINE IS LOAD-BEARING. This fragment must render with a
-- trailing newline, and the loader strips exactly one. Deleting the blank line
-- below silently joins the next clause onto the JOIN line.
  LEFT JOIN np ON np.chain_id=t.chain_id AND np.token=t.sell_token

