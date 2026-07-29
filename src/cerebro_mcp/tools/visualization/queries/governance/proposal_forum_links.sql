
WITH p AS (
  SELECT id, @gip_title AS gip_number,
         @discussion_topic_sql AS discussion_topic_id
  FROM governance_db.snapshot_proposals FINAL
  WHERE id = {proposal_id:String}
)
SELECT linked_type, linked_id, linked_title, link_source, activity_count,
       activity_at
FROM (
  SELECT 'forum_topic' AS linked_type, toString(t.id) AS linked_id,
         t.title AS linked_title, 'discussion' AS link_source,
         t.posts_count AS activity_count, t.last_posted_at AS activity_at
  FROM governance_db.forum_topics AS t FINAL
  WHERE t.id IN (SELECT discussion_topic_id FROM p
                 WHERE discussion_topic_id IS NOT NULL)
  UNION ALL
  SELECT 'forum_topic', toString(t.id), t.title, 'gip', t.posts_count,
         t.last_posted_at
  FROM governance_db.forum_topics AS t FINAL
  WHERE @gip_topic_title IS NOT NULL
    AND @gip_topic_title IN (SELECT gip_number FROM p
                                  WHERE gip_number IS NOT NULL)
    AND t.id NOT IN (SELECT discussion_topic_id FROM p
                     WHERE discussion_topic_id IS NOT NULL)
  UNION ALL
  SELECT 'proposal', s.id, s.title, 'gip', s.votes_count, s.created_at
  FROM governance_db.snapshot_proposals AS s FINAL
  WHERE s.id != {proposal_id:String}
    AND @gip_proposal_title IS NOT NULL
    AND @gip_proposal_title IN (SELECT gip_number FROM p
                                  WHERE gip_number IS NOT NULL)
)
ORDER BY link_source, linked_type, linked_id
