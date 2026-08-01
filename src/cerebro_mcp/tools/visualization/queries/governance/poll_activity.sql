-- LONG activity series at poll grain (mirrors forum_activity.sql: one row
-- per (bucket, metric), pivoted client-side). The polls CTE collapses the
-- option grain first; ClickHouse inlines a CTE once per reference, so the
-- two UNION arms scan it twice — a conscious exception, acceptable at a few
-- hundred rows. Do NOT copy this shape to a large table.
-- poll_voters attributes each poll's participant total to its CREATION
-- bucket: forum_polls has no per-vote timestamps, and creation time is the
-- poll-bearing post's created_at (polls are not always in the opening post).
-- This query never reads option_votes, so the -1 hidden-results sentinel
-- cannot reach it.
WITH polls AS (
  SELECT p.poll_id AS poll_id,
         any(fp.created_at) AS created_at,
         max(p.voters) AS voters
  FROM governance_db.forum_polls AS p FINAL
  INNER JOIN governance_db.forum_posts AS fp FINAL ON fp.id = p.post_id
  WHERE p.topic_id IN (
    SELECT id FROM governance_db.forum_topics FINAL WHERE @filter_sql
  )
  GROUP BY p.poll_id
)
SELECT bucket, metric, metric_value, '@unit' AS bucket_unit
FROM (
  SELECT @poll_bucket AS bucket, 'polls_created' AS metric,
         count() AS metric_value
  FROM polls
  WHERE @polls_time
  GROUP BY bucket
  UNION ALL
  SELECT @poll_bucket AS bucket, 'poll_voters', sum(voters)
  FROM polls
  WHERE @polls_time
  GROUP BY bucket
)
ORDER BY bucket, metric
