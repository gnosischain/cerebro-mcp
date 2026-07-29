
SELECT *
FROM api_execution_yields_user_fee_collections_daily
WHERE lower(provider) = {address:String}
ORDER BY date DESC, fees_usd DESC
LIMIT 2000

