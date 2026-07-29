
SELECT u.id AS user_id, u.username, u.name, u.trust_level,
       u.post_count AS lifetime_posts, u.topic_count AS lifetime_topics,
       u.likes_received, u.likes_given, u.days_visited,
       coalesce(p.posts_in_range, 0) AS posts_in_range,
       coalesce(p.topics_started, 0) AS topics_started,
       p.last_post_at AS last_post_at
FROM governance_db.forum_users AS u FINAL
LEFT JOIN (
  SELECT user_id, count() AS posts_in_range,
         countIf(post_number = 1) AS topics_started,
         max(created_at) AS last_post_at
  FROM governance_db.forum_posts FINAL
  WHERE @posts_time
  GROUP BY user_id
) AS p ON toInt64(p.user_id) = toInt64(u.id)
ORDER BY posts_in_range DESC, user_id
