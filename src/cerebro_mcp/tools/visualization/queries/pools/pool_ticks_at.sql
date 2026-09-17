-- The raw initialized ticks behind the profile. liquidity_net is signed and its
-- running sum is what the profile is built from, so showing both makes the
-- derivation checkable rather than asking the reader to trust it.
@asof_cte,
@st_cte
SELECT
  t.tick AS tick,
  toString(t.liquidity_gross) AS liquidity_gross_raw,
  toFloat64(t.liquidity_gross) AS liquidity_gross_float,
  toString(t.liquidity_net) AS liquidity_net_raw,
  toFloat64(t.liquidity_net) AS liquidity_net_float,
  toString(t.fee_growth_outside_0_x128) AS fee_growth_outside_0_raw,
  toString(t.fee_growth_outside_1_x128) AS fee_growth_outside_1_raw,
  pow(1.0001, t.tick) AS price_raw_at_tick,
  t.tick < (SELECT max(current_tick) FROM st) AS is_below_current
FROM @db.@view AS t
WHERE t.chain_id = @chain AND t.job_name = '@job'
  AND t.pool_address = {pool:String}
  AND t.snapshot_date IN (SELECT as_of FROM asof)
ORDER BY tick
