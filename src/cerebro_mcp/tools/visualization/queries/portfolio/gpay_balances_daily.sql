
SELECT *
FROM api_execution_gpay_user_balances_daily
WHERE lower(wallet_address) = {address:String}
ORDER BY date DESC, value_usd DESC
LIMIT 2000

