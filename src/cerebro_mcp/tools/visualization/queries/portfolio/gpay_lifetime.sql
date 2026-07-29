
SELECT *
FROM api_execution_gpay_user_lifetime_metrics
WHERE lower(wallet_address) = {address:String}
LIMIT 1

