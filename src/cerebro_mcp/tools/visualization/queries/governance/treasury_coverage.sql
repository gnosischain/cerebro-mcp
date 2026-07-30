@asof_cte,
held AS (
  SELECT t.chain_id AS chain_id,
         t.token_address AS token_address,
         anyHeavy(t.symbol) AS symbol,
         anyHeavy(t.decimals) AS decimals,
         anyHeavy(t.metadata_status) AS metadata_status
  FROM @src AS t
  INNER JOIN asof AS a ON t.chain_id = a.chain_id AND t.snapshot_date = a.as_of
  WHERE t.job_name = '@job' AND @chain_sql AND @ltd_sql
    AND t.balance_raw != 0
  GROUP BY t.chain_id, t.token_address
),
totals AS (
  SELECT countIf(symbol IS NOT NULL) AS symbol_known,
         countIf(symbol IS NULL) AS symbol_unknown,
         countIf(decimals IS NOT NULL) AS decimals_known,
         countIf(decimals IS NULL) AS decimals_unknown,
         countIf(metadata_status = 'resolved') AS metadata_known,
         countIf(metadata_status != 'resolved') AS metadata_unknown,
         count() AS held_total
  FROM held
)
SELECT dimension, known, unknown,
       known / nullIf(known + unknown, 0) AS pct_known
FROM totals
-- `usd_price` is hardcoded to 0-known BY CONSTRUCTION, and that is not a
-- measurement: this plane has no price source at all. Valuation arrives on the
-- CLIENT, from the CoinGecko overlay (tools/visualization/coingecko.py), and is
-- never written back here.
--
-- Naming the dimension anyway is deliberate — silently omitting it would read as
-- "pricing is not a concern on this plane" when it is the single biggest gap.
-- But the row must not be read as "0 tokens are priceable": the board two panels
-- below routinely shows a live figure (62 of 231 held mainnet tokens at the last
-- measurement). The `basis` string on this dataset says so; without that, the
-- two panels flatly contradict each other.
ARRAY JOIN
  ['decimals', 'metadata', 'symbol', 'usd_price'] AS dimension,
  [decimals_known, metadata_known, symbol_known, toUInt64(0)] AS known,
  [decimals_unknown, metadata_unknown, symbol_unknown, held_total] AS unknown
ORDER BY dimension
