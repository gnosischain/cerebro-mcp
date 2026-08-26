
SELECT entity_type, identifier, label, role, evidence_count, match_rank
FROM (
  SELECT 'forum_topic' AS entity_type, toString(id) AS identifier,
         title AS label, 'forum_topic' AS role,
         toInt64(posts_count) AS evidence_count, 0 AS match_rank
  FROM governance_db.forum_topics FINAL
  WHERE id = {n:UInt32}
  UNION ALL
  -- De-identified label (WL-039): the caller typed the id; the candidate
  -- label adds no name. Names stay only on verbatim-post surfaces.
  SELECT 'forum_user', toString(id), concat('User #', toString(id)), 'forum_user',
         toInt64(post_count), 0
  FROM governance_db.forum_users FINAL
  WHERE id = {n:UInt32}
  UNION ALL
@gip_arms
)
ORDER BY match_rank, entity_type, identifier
