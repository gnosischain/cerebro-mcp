
SELECT *
FROM api_execution_circles_v2_avatar_token_distribution
WHERE lower(avatar) = {avatar:String}
ORDER BY balance_demurraged DESC, holder_category ASC
LIMIT 1000

