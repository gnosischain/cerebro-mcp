-- No username/name (WL-039 privacy alignment): the profile keys on user_id;
-- names stay only on verbatim-post surfaces (topic_posts, search_text).
SELECT id AS user_id, trust_level, likes_received,
       likes_given, post_count AS lifetime_posts,
       topic_count AS lifetime_topics, days_visited
FROM governance_db.forum_users FINAL
WHERE id = {user_id:UInt32}
ORDER BY user_id
