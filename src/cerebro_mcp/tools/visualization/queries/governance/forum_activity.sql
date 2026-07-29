
WITH ft AS (
  SELECT id, created_at FROM governance_db.forum_topics FINAL
  WHERE @filter_sql
)
SELECT bucket, metric, metric_value, '@unit' AS bucket_unit
FROM (
  SELECT @topic_bucket AS bucket, 'topics_created' AS metric,
         count() AS metric_value
  FROM ft
  WHERE @posts_time
  GROUP BY bucket
  UNION ALL
  SELECT @post_bucket AS bucket, 'posts_created', count()
  FROM governance_db.forum_posts FINAL
  WHERE @posts_time AND topic_id IN (SELECT id FROM ft)
  GROUP BY bucket
)
ORDER BY bucket, metric
