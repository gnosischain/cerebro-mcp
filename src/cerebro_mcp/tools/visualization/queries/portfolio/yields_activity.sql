
SELECT *
FROM api_execution_yields_user_activity
WHERE lower(wallet_address) = {address:String}
ORDER BY block_timestamp DESC
LIMIT 2000

