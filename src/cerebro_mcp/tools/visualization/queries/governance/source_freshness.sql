-- Two clocks per source. The forum INGESTION clock is the WEAKEST LINK: the
-- min across every forum table's own max(ingested_at) — topics, posts,
-- users, categories, polls, likes — so a stalled ingest of ANY one table
-- trips the STALE chip; a fresh posts/topics run must not mask stale likes
-- or polls. This is deliberately stricter than the old max() semantics.
-- Snapshot keeps max(): its three tables land in one ingest run. The
-- activity clock stays the max across event timestamps; users, categories
-- and polls carry no event timestamp and contribute NULL (never a fake
-- epoch-zero) — poll creation activity is already covered by forum_posts.
-- These are freshness reads on ingested_at only: the like eligibility
-- contract (hidden/deleted/mapped) deliberately does NOT apply here, or a
-- purge could fake freshness.
SELECT source,
       if(source = 'forum', min(latest_ingested_at), max(latest_ingested_at))
         AS latest_ingested_at,
       max(latest_activity_at) AS latest_activity_at
FROM (
  SELECT 'snapshot' AS source, max(ingested_at) AS latest_ingested_at,
         toNullable(max(created_at)) AS latest_activity_at
  FROM governance_db.snapshot_proposals FINAL
  UNION ALL
  SELECT 'snapshot', max(ingested_at), toNullable(max(created_at))
  FROM governance_db.snapshot_votes FINAL
  UNION ALL
  SELECT 'snapshot', max(ingested_at), toNullable(max(created_at))
  FROM governance_db.snapshot_follows FINAL
  UNION ALL
  SELECT 'forum', max(ingested_at), toNullable(max(last_posted_at))
  FROM governance_db.forum_topics FINAL
  UNION ALL
  SELECT 'forum', max(ingested_at), toNullable(max(created_at))
  FROM governance_db.forum_posts FINAL
  UNION ALL
  SELECT 'forum', max(ingested_at), CAST(NULL, 'Nullable(DateTime)')
  FROM governance_db.forum_users FINAL
  UNION ALL
  SELECT 'forum', max(ingested_at), CAST(NULL, 'Nullable(DateTime)')
  FROM governance_db.forum_categories FINAL
  UNION ALL
  SELECT 'forum', max(ingested_at), CAST(NULL, 'Nullable(DateTime)')
  FROM governance_db.forum_polls FINAL
  UNION ALL
  SELECT 'forum', max(ingested_at), toNullable(max(created_at))
  FROM governance_db.forum_likes FINAL
)
GROUP BY source
ORDER BY source
