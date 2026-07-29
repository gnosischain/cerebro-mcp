
WITH latest AS (
  SELECT max(date) AS max_date
  FROM api_execution_gpay_user_balances_daily
  WHERE lower(wallet_address) = {address:String}
)
SELECT
  wallet_address,
  date,
  token,
  label,
  value_native,
  value_usd
FROM api_execution_gpay_user_balances_daily
WHERE lower(wallet_address) = {address:String}
  AND date = (SELECT max_date FROM latest)
ORDER BY value_usd DESC, token ASC
LIMIT 200

