
SELECT t.id AS topic_id, t.title AS title, t.slug AS slug,
       t.category_id AS category_id, c.name AS category_name,
       t.posts_count AS posts_count, t.reply_count AS reply_count,
       t.views AS views, t.like_count AS like_count,
       t.participant_count AS participant_count, t.tags AS tags,
       t.created_at AS created_at, t.last_posted_at AS last_posted_at,
       t.bumped_at AS bumped_at, t.closed AS closed, t.archived AS archived,
       t.pinned AS pinned,
       multiIf(t.archived = 1, 'archived', t.closed = 1, 'closed', 'open') AS status,
       @gip_topic_title AS gip_number,
       concat('@forum_base_url/t/', t.slug, '/', toString(t.id)) AS topic_url
FROM governance_db.forum_topics AS t FINAL
LEFT JOIN governance_db.forum_categories AS c FINAL
  ON toInt64(c.id) = toInt64(t.category_id)
WHERE t.id = {topic_id:UInt32}
ORDER BY topic_id
