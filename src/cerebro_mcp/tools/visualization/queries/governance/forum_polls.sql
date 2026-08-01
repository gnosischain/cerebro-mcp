-- One row per poll. forum_polls is poll-OPTION grain and poll_id is the poll
-- identity (some posts carry TWO polls, so post_id alone under-groups).
-- voters is the poll-level total repeated per option row: max(), never sum().
-- option_votes = -1 is Discourse's hidden-results sentinel: every derived
-- column routes through a min() < 0 guard and raw option_votes is never
-- summed. leading_option is NULL when results are hidden, tied (a
-- positive-score tie only — an all-zero poll has no leader), or no votes;
-- leading_votes = 0 signals the no-votes case to the UI, which renders
-- Hidden / Tie / No votes respectively. Poll creation time is the
-- poll-bearing post's created_at (polls are NOT always in the opening
-- post), so the time window sits in HAVING on the aggregated alias.
SELECT p.poll_id AS poll_id,
       any(p.topic_id) AS topic_id,
       any(t.title) AS topic_title,
       any(p.post_id) AS post_id,
       any(fp.post_number) AS post_number,
       any(p.poll_name) AS poll_name,
       any(p.poll_type) AS poll_type,
       any(p.status) AS status,
       any(p.results_visibility) AS results_visibility,
       any(p.close_at) AS close_at,
       any(fp.created_at) AS created_at,
       count() AS options_count,
       max(p.voters) AS voters,
       min(p.option_votes) < 0 AS results_hidden,
       if(min(p.option_votes) < 0 OR max(p.option_votes) <= 0
          OR arrayCount(x -> x = arrayMax(groupArray(p.option_votes)),
                        groupArray(p.option_votes)) > 1,
          NULL,
          argMax(extractTextFromHTML(p.option_html), p.option_votes)
       ) AS leading_option,
       if(min(p.option_votes) < 0, NULL, max(p.option_votes)) AS leading_votes,
       if(min(p.option_votes) >= 0 AND max(p.option_votes) > 0
          AND arrayCount(x -> x = arrayMax(groupArray(p.option_votes)),
                         groupArray(p.option_votes)) > 1,
          1, 0
       ) AS leading_tied
FROM governance_db.forum_polls AS p FINAL
INNER JOIN governance_db.forum_posts AS fp FINAL ON fp.id = p.post_id
LEFT JOIN governance_db.forum_topics AS t FINAL
  ON toInt64(t.id) = toInt64(p.topic_id)
WHERE p.topic_id IN (
  SELECT id FROM governance_db.forum_topics FINAL WHERE @filter_sql
)
GROUP BY p.poll_id
HAVING @polls_time
ORDER BY created_at DESC, poll_id
