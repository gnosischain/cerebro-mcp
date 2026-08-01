-- Poll KPIs at poll grain. forum_polls is poll-OPTION grain: voters is the
-- poll-level participant total REPEATED on every option row, so per_poll
-- collapses the grain first (max(voters), never summed across options);
-- summing voters across POLLS is then participant-slots — a user voting in
-- N polls counts N times, hence the poll_voter_slots name. Poll creation
-- time is the poll-bearing post's created_at (forum_polls carries no
-- timestamp of its own, and polls are NOT always in the opening post).
-- option_votes = -1 is Discourse's hidden-results sentinel: it only feeds
-- the min() < 0 flag here, never a sum.
WITH per_poll AS (
  SELECT p.poll_id AS poll_id,
         any(p.topic_id) AS topic_id,
         any(p.status) AS status,
         any(p.poll_type) AS poll_type,
         max(p.voters) AS voters,
         min(p.option_votes) < 0 AS results_hidden,
         any(fp.created_at) AS created_at
  FROM governance_db.forum_polls AS p FINAL
  INNER JOIN governance_db.forum_posts AS fp FINAL ON fp.id = p.post_id
  WHERE p.topic_id IN (
    SELECT id FROM governance_db.forum_topics FINAL WHERE @filter_sql
  )
  GROUP BY p.poll_id
)
SELECT count() AS poll_count,
       countIf(status = 'open') AS open_polls,
       countIf(status = 'closed') AS closed_polls,
       countIf(poll_type = 'multiple') AS multiple_choice_polls,
       countIf(results_hidden) AS hidden_result_polls,
       uniqExact(topic_id) AS topics_with_polls,
       sum(voters) AS poll_voter_slots,
       max(created_at) AS latest_poll_at
FROM per_poll
WHERE @polls_time
ORDER BY poll_count
