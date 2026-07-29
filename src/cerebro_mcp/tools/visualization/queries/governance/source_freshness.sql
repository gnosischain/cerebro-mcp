
SELECT source,
       max(latest_ingested_at) AS latest_ingested_at,
       max(latest_activity_at) AS latest_activity_at
FROM (
  SELECT 'snapshot' AS source, max(ingested_at) AS latest_ingested_at,
         max(created_at) AS latest_activity_at
  FROM governance_db.snapshot_proposals FINAL
  UNION ALL
  SELECT 'snapshot', max(ingested_at), max(created_at)
  FROM governance_db.snapshot_votes FINAL
  UNION ALL
  SELECT 'snapshot', max(ingested_at), max(created_at)
  FROM governance_db.snapshot_follows FINAL
  UNION ALL
  SELECT 'forum', max(ingested_at), max(last_posted_at)
  FROM governance_db.forum_topics FINAL
  UNION ALL
  SELECT 'forum', max(ingested_at), max(created_at)
  FROM governance_db.forum_posts FINAL
)
GROUP BY source
ORDER BY source
