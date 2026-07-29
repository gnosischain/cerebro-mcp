
WITH topics AS (
  SELECT @gip_title AS gip,
         argMax(id, last_posted_at) AS topic_id,
         argMax(title, last_posted_at) AS label,
         min(created_at) AS first_seen,
         max(last_posted_at) AS last_activity,
         sum(posts_count) AS posts,
         max(participant_count) AS participants,
         sum(views) AS views,
         argMax(category_id, last_posted_at) AS category_id,
         -- Highest phase tag seen across this GIP's topics. phase-3 outranks
         -- phase-2 outranks phase-1, so max() over the label is the lifecycle
         -- position without a CASE ladder.
         max(arrayFirst(x -> x LIKE 'phase-%', splitByChar(',', tags))) AS phase
  FROM @gov_db.forum_topics FINAL
  WHERE @gip_title IS NOT NULL
  GROUP BY gip
),
props AS (
  SELECT @gip_title AS gip,
         count() AS proposals,
         argMax(id, created_at) AS proposal_id,
         argMax(title, created_at) AS proposal_title,
         argMax(state, created_at) AS state,
         argMax(author, created_at) AS author,
         argMax(@quorum_status_sql, created_at) AS quorum_status,
         sum(votes_count) AS votes,
         min(created_at) AS proposal_at,
         max(end_at) AS voting_ended
  FROM @gov_db.snapshot_proposals FINAL
  WHERE @gip_title IS NOT NULL
  GROUP BY gip
)
SELECT gip,
       -- A GIP with no forum topic still gets a label: the proposal title. A
       -- node with neither cannot exist (the gip key comes from one of them).
       if(t.label != '', t.label, p.proposal_title) AS label,
       t.topic_id AS topic_id,
       p.proposal_id AS proposal_id,
       -- The lifecycle stage this GIP reached, as EVIDENCE not inference:
       -- 'voted' means a Snapshot proposal exists, the phase tags are the
       -- forum's own, and 'unstaged' means neither is recorded.
       multiIf(p.proposals > 0, 'voted', t.phase != '', t.phase, 'unstaged') AS stage,
       p.state AS proposal_state,
       p.quorum_status AS quorum_status,
       p.author AS author,
       t.posts AS posts,
       t.participants AS participants,
       t.views AS views,
       t.category_id AS category_id,
       p.votes AS votes,
       -- The x-axis of the timeline view. GIP NUMBER is only 89% monotone with
       -- date (17 inversions across 148 pairs), so the number is a label, never
       -- the chronology. Falls back to the proposal date for a GIP that reached
       -- a vote without an indexed forum thread.
       if(t.first_seen > toDateTime(0), t.first_seen, p.proposal_at) AS first_seen,
       greatest(t.last_activity, p.voting_ended) AS last_activity,
       t.topic_id > 0 AS has_topic,
       p.proposals > 0 AS has_proposal
FROM topics AS t
FULL OUTER JOIN props AS p USING (gip)
WHERE gip IS NOT NULL AND gip > 0
ORDER BY gip
