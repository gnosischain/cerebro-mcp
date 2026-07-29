
SELECT count() AS topic_count,
       sum(posts_count) AS post_count,
       sum(views) AS view_count,
       sum(like_count) AS like_count,
       sum(participant_count) AS participant_count,
       countIf(closed = 0 AND archived = 0) AS open_count,
       countIf(closed = 1 AND archived = 0) AS closed_count,
       countIf(archived = 1) AS archived_count,
       (SELECT uniqExact(user_id) FROM governance_db.forum_posts FINAL
        WHERE @posts_time AND user_id > 0 AND topic_id IN (
          SELECT id FROM governance_db.forum_topics FINAL WHERE @topic_where
        )) AS active_users,
       (SELECT uniqExact(category_id) FROM governance_db.forum_topics FINAL
        WHERE @topic_where) AS active_categories
FROM governance_db.forum_topics FINAL
WHERE @topic_where
ORDER BY topic_count
