
SELECT entity_type, identifier, label, role, evidence_count, match_rank
FROM (
  SELECT 'proposal' AS entity_type, id AS identifier, title AS label,
         'proposal_title' AS role, toInt64(votes_count) AS evidence_count,
         @rank_title AS match_rank
  FROM governance_db.snapshot_proposals FINAL
  WHERE positionCaseInsensitive(title, {q:String}) > 0
  ORDER BY match_rank, evidence_count DESC, identifier
  LIMIT 20
  UNION ALL
  SELECT 'forum_topic' AS entity_type, toString(id) AS identifier,
         title AS label, 'topic_title' AS role,
         toInt64(posts_count) AS evidence_count,
         @rank_title AS match_rank
  FROM governance_db.forum_topics FINAL
  WHERE positionCaseInsensitive(title, {q:String}) > 0
  ORDER BY match_rank, evidence_count DESC, identifier
  LIMIT 20
  UNION ALL
  SELECT 'forum_user' AS entity_type, toString(id) AS identifier,
         username AS label, 'forum_username' AS role,
         toInt64(post_count) AS evidence_count,
         @rank_username AS match_rank
  FROM governance_db.forum_users FINAL
  WHERE positionCaseInsensitive(username, {q:String}) > 0
  ORDER BY match_rank, evidence_count DESC, identifier
  LIMIT 20
)
ORDER BY match_rank, evidence_count DESC, entity_type, identifier
