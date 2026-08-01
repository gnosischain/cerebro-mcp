-- LONG likes series (mirrors forum_activity.sql: one row per (bucket,
-- metric), pivoted client-side). Eligibility contract for every analytical
-- forum_likes read: ACTIVE (hidden = 0 AND deleted = 0), MAPPED (the
-- referenced topic and post still exist — live rows DO reference deleted
-- content), SCOPED (@filter_sql category/status/query filters; the topic IN
-- subquery enforces both mapped and scoped for topics). Excluded rows are
-- counted and disclosed by forum_summary, never silently dropped here.
-- Attributed likes undercount the like_count counters (Discourse who-liked
-- visibility limit) — forum_summary computes the live coverage figure.
SELECT bucket, metric, metric_value, '@unit' AS bucket_unit
FROM (
  SELECT @like_bucket AS bucket, 'likes_given' AS metric,
         count() AS metric_value
  FROM governance_db.forum_likes FINAL
  WHERE hidden = 0 AND deleted = 0 AND @likes_time
    AND topic_id IN (
      SELECT id FROM governance_db.forum_topics FINAL WHERE @filter_sql
    )
    AND post_id IN (SELECT id FROM governance_db.forum_posts FINAL)
  GROUP BY bucket
  UNION ALL
  SELECT @like_bucket AS bucket, 'distinct_likers', uniqExact(acting_user_id)
  FROM governance_db.forum_likes FINAL
  WHERE hidden = 0 AND deleted = 0 AND @likes_time
    AND topic_id IN (
      SELECT id FROM governance_db.forum_topics FINAL WHERE @filter_sql
    )
    AND post_id IN (SELECT id FROM governance_db.forum_posts FINAL)
  GROUP BY bucket
)
ORDER BY bucket, metric
