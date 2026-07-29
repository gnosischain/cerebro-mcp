
SELECT *
FROM api_execution_circles_v2_avatar_mint_activity_daily
WHERE lower(avatar) = {avatar:String}
ORDER BY date DESC
LIMIT 2000

