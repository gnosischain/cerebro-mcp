-- likes_received/likes_given are the LIFETIME counter columns on forum_users
-- (the coverage truth). The *_in_range columns read the per-like table under
-- the eligibility contract: ACTIVE (hidden = 0 AND deleted = 0), MAPPED (the
-- receive arm's INNER JOIN to forum_posts enforces post-mapped and resolves
-- the post AUTHOR; the give arm checks posts by IN), SCOPED (@filter_sql
-- topic subquery — filters only, never a last_posted_at window). Attributed
-- likes undercount the counters (Discourse who-liked visibility limit) —
-- the UI carries the disclosure. Two time tokens because the receive arm
-- must prefix the predicate column (both joined tables carry created_at).
SELECT u.id AS user_id, u.username, u.name, u.trust_level,
       u.post_count AS lifetime_posts, u.topic_count AS lifetime_topics,
       u.likes_received, u.likes_given, u.days_visited,
       coalesce(p.posts_in_range, 0) AS posts_in_range,
       coalesce(p.topics_started, 0) AS topics_started,
       p.last_post_at AS last_post_at,
       coalesce(lr.likes_received_in_range, 0) AS likes_received_in_range,
       coalesce(lg.likes_given_in_range, 0) AS likes_given_in_range
FROM governance_db.forum_users AS u FINAL
LEFT JOIN (
  SELECT user_id, count() AS posts_in_range,
         countIf(post_number = 1) AS topics_started,
         max(created_at) AS last_post_at
  FROM governance_db.forum_posts FINAL
  WHERE @posts_time
  GROUP BY user_id
) AS p ON toInt64(p.user_id) = toInt64(u.id)
LEFT JOIN (
  SELECT fp.user_id AS user_id, count() AS likes_received_in_range
  FROM governance_db.forum_likes AS l FINAL
  INNER JOIN governance_db.forum_posts AS fp FINAL ON fp.id = l.post_id
  WHERE l.hidden = 0 AND l.deleted = 0 AND @likes_recv_time
    AND l.topic_id IN (
      SELECT id FROM governance_db.forum_topics FINAL WHERE @filter_sql
    )
  GROUP BY fp.user_id
) AS lr ON toInt64(lr.user_id) = toInt64(u.id)
LEFT JOIN (
  SELECT acting_user_id, count() AS likes_given_in_range
  FROM governance_db.forum_likes FINAL
  WHERE hidden = 0 AND deleted = 0 AND @likes_given_time
    AND topic_id IN (
      SELECT id FROM governance_db.forum_topics FINAL WHERE @filter_sql
    )
    AND post_id IN (SELECT id FROM governance_db.forum_posts FINAL)
  GROUP BY acting_user_id
) AS lg ON toInt64(lg.acting_user_id) = toInt64(u.id)
ORDER BY posts_in_range DESC, user_id
