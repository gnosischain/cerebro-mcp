-- Likes per (bucket, forum category) — feeds the stacked bars of the Forum
-- tab's likes chart (the unique-likers line comes from likes_activity, same
-- bucketing and filters). Eligibility contract as everywhere: ACTIVE
-- (hidden = 0 AND deleted = 0), MAPPED (topic via the INNER JOIN, post via
-- IN), SCOPED (@filter_sql subquery). Time and bucket expressions prefix
-- l.created_at because forum_topics carries a created_at too. The client
-- keeps the top categories and folds the remainder into a COUNTED
-- "Other categories" series — never silently dropped.
SELECT @like_bucket AS bucket, '@unit' AS bucket_unit,
       if(c.name = '', 'Uncategorized', c.name) AS category,
       count() AS likes
FROM governance_db.forum_likes AS l FINAL
INNER JOIN governance_db.forum_topics AS t FINAL
  ON toInt64(t.id) = toInt64(l.topic_id)
LEFT JOIN governance_db.forum_categories AS c FINAL
  ON toInt64(c.id) = toInt64(t.category_id)
WHERE l.hidden = 0 AND l.deleted = 0 AND @likes_time
  AND l.topic_id IN (
    SELECT id FROM governance_db.forum_topics FINAL WHERE @filter_sql
  )
  AND l.post_id IN (SELECT id FROM governance_db.forum_posts FINAL)
GROUP BY bucket, category
ORDER BY bucket, category
