-- Section KPIs. like_count/view_count etc. are the LIFETIME counter columns
-- on forum_topics (100% coverage). The like columns below read the per-like
-- table under the eligibility contract — ACTIVE (hidden = 0 AND deleted = 0),
-- MAPPED (referenced topic and post still exist), SCOPED (@likes_topic_filter
-- carries ONLY category/status/query filters; @topic_where would also window
-- topics by last_posted_at and silently drop valid in-range likes). Rows
-- failing active or mapped are COUNTED (likes_hidden_or_deleted,
-- likes_unmapped — the latter against the unfiltered range: its topic no
-- longer exists to filter on), never silently dropped.
-- like_attribution_pct is the ALL-HISTORY share of counter-tracked likes the
-- per-like table attributes (Discourse who-liked visibility limit); the UI
-- renders this live figure instead of a hard-coded percentage. NULL, never
-- 0, if the counter denominator is empty.
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
        WHERE @topic_where) AS active_categories,
       (SELECT count() FROM governance_db.forum_likes FINAL
        WHERE hidden = 0 AND deleted = 0 AND @likes_time AND topic_id IN (
          SELECT id FROM governance_db.forum_topics FINAL WHERE @likes_topic_filter
        ) AND post_id IN (SELECT id FROM governance_db.forum_posts FINAL)
       ) AS likes_in_range,
       (SELECT uniqExact(acting_user_id) FROM governance_db.forum_likes FINAL
        WHERE hidden = 0 AND deleted = 0 AND @likes_time AND topic_id IN (
          SELECT id FROM governance_db.forum_topics FINAL WHERE @likes_topic_filter
        ) AND post_id IN (SELECT id FROM governance_db.forum_posts FINAL)
       ) AS distinct_likers,
       (SELECT countIf(hidden != 0 OR deleted != 0)
        FROM governance_db.forum_likes FINAL
        WHERE @likes_time
       ) AS likes_hidden_or_deleted,
       (SELECT countIf(
          topic_id NOT IN (SELECT id FROM governance_db.forum_topics FINAL)
          OR post_id NOT IN (SELECT id FROM governance_db.forum_posts FINAL))
        FROM governance_db.forum_likes FINAL
        WHERE hidden = 0 AND deleted = 0 AND @likes_time
       ) AS likes_unmapped,
       (SELECT count() FROM governance_db.forum_likes FINAL
        WHERE hidden = 0 AND deleted = 0
          AND topic_id IN (SELECT id FROM governance_db.forum_topics FINAL)
          AND post_id IN (SELECT id FROM governance_db.forum_posts FINAL))
       / nullIf((SELECT sum(like_count) FROM governance_db.forum_posts FINAL), 0)
         AS like_attribution_pct
FROM governance_db.forum_topics FINAL
WHERE @topic_where
ORDER BY topic_count
