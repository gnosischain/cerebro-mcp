
SELECT kind, identifier, title, status, activity_at
FROM (
  SELECT 'proposal' AS kind, id AS identifier, title, state AS status,
         created_at AS activity_at
  FROM governance_db.snapshot_proposals FINAL
  ORDER BY created_at DESC, id
  LIMIT 8
  UNION ALL
  SELECT 'forum_topic', toString(id), title,
         multiIf(archived = 1, 'archived', closed = 1, 'closed', 'open'),
         bumped_at
  FROM governance_db.forum_topics FINAL
  ORDER BY bumped_at DESC, id
  LIMIT 8
)
ORDER BY activity_at DESC, kind, identifier
