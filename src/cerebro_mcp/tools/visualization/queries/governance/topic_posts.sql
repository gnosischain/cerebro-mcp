
SELECT id AS post_id, post_number, user_id, username, created_at, updated_at,
       reply_to_post_number, reply_count, reads, like_count,
       raw AS raw_markdown, cooked AS cooked_html,
       extractTextFromHTML(cooked) AS plain_text
FROM governance_db.forum_posts FINAL
WHERE topic_id = {topic_id:UInt32}
ORDER BY post_number, post_id
