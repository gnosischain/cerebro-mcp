
SELECT *
FROM api_execution_gpay_user_activity
WHERE lower(wallet_address) = {address:String}
ORDER BY timestamp DESC
LIMIT 2000

