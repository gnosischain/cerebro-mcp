
SELECT *
FROM api_execution_yields_user_lending_positions
WHERE lower(user_address) = {address:String}
ORDER BY balance_usd DESC, protocol ASC
LIMIT 2000

