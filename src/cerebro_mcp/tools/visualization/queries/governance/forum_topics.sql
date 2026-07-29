
SELECT id, title, slug, category_id, posts_count, reply_count, views,
       like_count, participant_count, tags, created_at, last_posted_at,
       bumped_at, closed, archived, pinned,
       multiIf(archived = 1, 'archived', closed = 1, 'closed', 'open') AS status,
       @gip_sql AS gip_number
FROM governance_db.forum_topics FINAL
WHERE @topic_where
ORDER BY @sort_fragment
