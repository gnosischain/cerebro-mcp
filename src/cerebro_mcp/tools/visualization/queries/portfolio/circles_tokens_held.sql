
SELECT
  avatar,
  tokens_held_count
FROM api_execution_circles_v2_avatar_tokens_held_count
WHERE lower(avatar) = {avatar:String}
LIMIT 1

