
SELECT *
FROM api_execution_circles_v2_avatar_metadata
WHERE lower(avatar) = {avatar:String}
LIMIT 1

