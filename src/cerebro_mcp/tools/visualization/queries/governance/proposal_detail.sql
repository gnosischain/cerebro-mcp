
SELECT id, space_id, title, state, type, author, discussion, created_at,
       start_at, end_at, snapshot_block, scores_total, quorum, votes_count,
       scores_state,
       @quorum_ratio_sql AS quorum_ratio,
       @quorum_status_sql AS quorum_status,
       @gip_title AS gip_number,
       @discussion_topic_sql AS discussion_topic_id,
       JSONExtractString(raw_json, 'body') AS body_markdown,
       JSONExtractRaw(raw_json, 'choices') AS choices_json,
       JSONExtractRaw(raw_json, 'scores') AS scores_json,
       concat('https://snapshot.org/#/', space_id, '/proposal/', id) AS snapshot_url
FROM governance_db.snapshot_proposals FINAL
WHERE id = {proposal_id:String}
ORDER BY id
