
SELECT bucket, metric, metric_value, '@unit' AS bucket_unit
FROM (
  SELECT @proposal_bucket AS bucket, 'proposals_created' AS metric, count() AS metric_value
  FROM governance_db.snapshot_proposals FINAL WHERE @proposals_time
  GROUP BY bucket
  UNION ALL
  SELECT @vote_bucket AS bucket, 'votes_cast', count()
  FROM governance_db.snapshot_votes FINAL WHERE @votes_time
  GROUP BY bucket
  UNION ALL
  SELECT @topic_bucket AS bucket, 'topics_created', count()
  FROM governance_db.forum_topics FINAL WHERE @topics_time
  GROUP BY bucket
  UNION ALL
  SELECT @post_bucket AS bucket, 'posts_created', count()
  FROM governance_db.forum_posts FINAL WHERE @posts_time
  GROUP BY bucket
)
ORDER BY bucket, metric
