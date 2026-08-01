-- Per-OPTION poll rows for one topic (poll-level fields repeated per option
-- BY DESIGN; the frontend groups by poll_id and takes voters once, never
-- summed). nullIf(option_votes, -1) neutralizes Discourse's hidden-results
-- sentinel so hidden options reach the client as NULL scores. post_number
-- locates the poll-bearing post — polls are NOT always in the opening post.
-- LEFT JOIN keeps a poll visible even if its post row disappeared from the
-- index (post_number lands 0 and the UI omits the badge) — a silently
-- vanished poll would violate the visible-exclusion rule.
SELECT p.poll_id AS poll_id, p.post_id AS post_id,
       fp.post_number AS post_number,
       p.poll_name AS poll_name, p.poll_type AS poll_type,
       p.status AS status, p.results_visibility AS results_visibility,
       p.is_public AS is_public, p.close_at AS close_at,
       p.voters AS voters, p.option_id AS option_id,
       extractTextFromHTML(p.option_html) AS option_label,
       nullIf(p.option_votes, -1) AS option_votes
FROM governance_db.forum_polls AS p FINAL
LEFT JOIN governance_db.forum_posts AS fp FINAL ON fp.id = p.post_id
WHERE p.topic_id = {topic_id:UInt32}
ORDER BY poll_id, option_votes DESC NULLS LAST, option_id
