
SELECT *
FROM api_execution_circles_v2_avatar_balances_latest
WHERE lower(avatar) = {avatar:String}
ORDER BY balance_demurraged DESC, token_address ASC
LIMIT 1000

