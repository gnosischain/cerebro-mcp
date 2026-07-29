
SELECT toStartOfMonth(created_at) AS bucket, count() AS post_count,
       countIf(post_number = 1) AS topics_started, 'month' AS bucket_unit
FROM governance_db.forum_posts FINAL
WHERE user_id = {user_id:UInt32}
GROUP BY bucket
ORDER BY bucket
