
SELECT
  c.avatar,
  c.avatar_type,
  c.name,
  c.block_timestamp,
  m.metadata_name,
  m.metadata_preview_image_url
FROM api_execution_circles_v2_avatars_current c
LEFT JOIN api_execution_circles_v2_avatar_metadata m
  ON m.avatar = c.avatar
WHERE lower(c.avatar) = {address:String}
LIMIT 1

