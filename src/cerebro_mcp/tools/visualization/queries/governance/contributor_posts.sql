
SELECT p.id AS post_id, p.topic_id AS topic_id, t.title AS topic_title,
       p.post_number AS post_number, p.created_at AS created_at,
       p.like_count AS like_count, p.reads AS reads,
       substring(extractTextFromHTML(p.cooked), 1, 500) AS excerpt
FROM governance_db.forum_posts AS p FINAL
LEFT JOIN governance_db.forum_topics AS t FINAL ON t.id = p.topic_id
WHERE p.user_id = {user_id:UInt32}
ORDER BY p.created_at DESC, post_id
