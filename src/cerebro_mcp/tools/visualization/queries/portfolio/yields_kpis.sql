
SELECT *
FROM api_execution_yields_user_kpis
WHERE lower(wallet_address) = {address:String}
LIMIT 1

