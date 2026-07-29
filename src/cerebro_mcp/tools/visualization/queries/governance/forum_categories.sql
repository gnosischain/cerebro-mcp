
SELECT id AS category_id, parent_id, name, slug, topic_count, post_count,
       description
FROM governance_db.forum_categories FINAL
ORDER BY topic_count DESC, category_id
