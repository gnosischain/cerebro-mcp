
SELECT *
FROM api_execution_gpay_user_cashback_daily
WHERE lower(wallet_address) = {address:String}
ORDER BY date DESC, value DESC
LIMIT 2000

