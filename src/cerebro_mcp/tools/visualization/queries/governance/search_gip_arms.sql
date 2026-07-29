
  SELECT 'proposal' AS entity_type, id AS identifier, title AS label,
         'gip_proposal' AS role, toInt64(votes_count) AS evidence_count,
         0 AS match_rank
  FROM governance_db.snapshot_proposals FINAL
  WHERE @gip_title = {gip:Int32}
  UNION ALL
  SELECT 'forum_topic', toString(id), title, 'gip_topic',
         toInt64(posts_count), 0
  FROM governance_db.forum_topics FINAL
  WHERE @gip_title = {gip:Int32}
