
SELECT c.id AS category_id, c.name AS category_name, c.slug AS category_slug,
       coalesce(t.topics_in_range, 0) AS topics_in_range,
       coalesce(t.posts_in_range, 0) AS posts_in_range,
       t.latest_post_at AS last_posted_at
FROM governance_db.forum_categories AS c FINAL
LEFT JOIN (
  SELECT category_id, count() AS topics_in_range,
         sum(posts_count) AS posts_in_range,
         max(last_posted_at) AS latest_post_at
  FROM governance_db.forum_topics FINAL
  WHERE @activity_time
  GROUP BY category_id
) AS t ON toInt64(t.category_id) = toInt64(c.id)
ORDER BY topics_in_range DESC, category_id
