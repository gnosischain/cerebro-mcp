-- Top topics by ATTRIBUTED likes within the toolbar range — a different
-- question from the lifetime like_count sort on forum_topics (counter
-- columns, 100% coverage, not windowed). lifetime_like_count ships beside
-- likes_in_range so both truths sit in one row and the who-liked coverage
-- gap stays visible instead of hidden. Eligibility contract as in
-- likes_activity (active + mapped + scoped); filters live INSIDE the
-- like-side subquery, and the INNER JOIN to forum_topics enforces
-- topic-mapped.
SELECT t.id AS id, t.title AS title, t.category_id AS category_id,
       l.likes_in_range AS likes_in_range,
       l.likers_in_range AS likers_in_range,
       t.like_count AS lifetime_like_count,
       t.posts_count AS posts_count,
       l.last_like_at AS last_like_at,
       @gip_sql AS gip_number
FROM (
  SELECT topic_id, count() AS likes_in_range,
         uniqExact(acting_user_id) AS likers_in_range,
         max(created_at) AS last_like_at
  FROM governance_db.forum_likes FINAL
  WHERE hidden = 0 AND deleted = 0 AND @likes_time
    AND topic_id IN (
      SELECT id FROM governance_db.forum_topics FINAL WHERE @filter_sql
    )
    AND post_id IN (SELECT id FROM governance_db.forum_posts FINAL)
  GROUP BY topic_id
) AS l
INNER JOIN governance_db.forum_topics AS t FINAL
  ON toInt64(t.id) = toInt64(l.topic_id)
ORDER BY likes_in_range DESC, id
LIMIT 25
