
SELECT *
FROM api_execution_yields_user_lending_balances_daily
WHERE lower(user_address) = {address:String}
ORDER BY date DESC, balance_usd DESC
LIMIT 2000

