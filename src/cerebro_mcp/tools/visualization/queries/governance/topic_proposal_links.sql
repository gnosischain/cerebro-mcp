
WITH t AS (
  SELECT id, @gip_title AS gip_number
  FROM governance_db.forum_topics FINAL
  WHERE id = {topic_id:UInt32}
)
SELECT linked_id, linked_title, state, link_source, votes_count, created_at
FROM (
  SELECT p.id AS linked_id, p.title AS linked_title, p.state AS state,
         'discussion' AS link_source, p.votes_count AS votes_count,
         p.created_at AS created_at
  FROM governance_db.snapshot_proposals AS p FINAL
  WHERE @discussion_topic_sql = {topic_id:UInt32}
  UNION ALL
  SELECT p.id, p.title, p.state, 'gip', p.votes_count, p.created_at
  FROM governance_db.snapshot_proposals AS p FINAL
  WHERE @gip_proposal_title IS NOT NULL
    AND @gip_proposal_title IN (SELECT gip_number FROM t
                                  WHERE gip_number IS NOT NULL)
    AND (@discussion_topic_sql IS NULL
         OR @discussion_topic_sql != {topic_id:UInt32})
)
ORDER BY link_source, linked_id
