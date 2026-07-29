
SELECT
  provider,
  pool_address,
  protocol,
  tick_lower,
  tick_upper,
  capital_in_usd,
  capital_out_usd,
  fees_collected_usd,
  is_active,
  is_in_range,
  pool_current_tick,
  entry_date,
  last_action_date
FROM api_execution_yields_user_lp_positions
WHERE lower(provider) = {address:String}
ORDER BY last_action_date DESC, protocol ASC
LIMIT 2000

