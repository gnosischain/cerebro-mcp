
SELECT id AS user_id, username, name, trust_level, likes_received,
       likes_given, post_count AS lifetime_posts,
       topic_count AS lifetime_topics, days_visited
FROM governance_db.forum_users FINAL
WHERE id = {user_id:UInt32}
ORDER BY user_id
