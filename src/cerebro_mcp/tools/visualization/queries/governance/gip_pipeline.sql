
WITH voted AS (
  -- GIP numbers that already reached a Snapshot vote. EXCLUDED below: a GIP
  -- that has been voted on is not moving toward a vote, it is past one, and
  -- listing it here (even labelled) makes the panel answer a different
  -- question than the one it asks.
  SELECT @gip_prop AS gip
  FROM @gov_db.snapshot_proposals FINAL
  WHERE @gip_prop IS NOT NULL
),
open_topics AS (
  SELECT t.id AS topic_id,
         t.title AS title,
         @gip_title AS gip,
         phase,
         t.posts_count AS posts_count,
         t.participant_count AS participant_count,
         t.views AS views,
         t.created_at AS created_at,
         t.last_posted_at AS last_posted_at,
         dateDiff('day', t.last_posted_at, now()) AS days_idle
  FROM @gov_db.forum_topics AS t FINAL
  ARRAY JOIN splitByChar(',', t.tags) AS phase
  -- closed/archived threads are settled discussions, not live ones.
  WHERE phase IN ('phase-1', 'phase-2') AND t.archived = 0 AND t.closed = 0
)
SELECT topic_id, title, gip, phase, posts_count, participant_count, views,
       created_at, last_posted_at, days_idle,
       -- How many pending topics this list is NOT showing because they have
       -- gone quiet. Carried on every row as a constant so the panel can state
       -- the exclusion instead of silently truncating: 149 of 157 open
       -- phase-1/2 topics have not been touched in six months, and a list that
       -- hid that would imply a pipeline that is not moving.
       (SELECT count() FROM open_topics
        WHERE days_idle > @idle_days
          AND (gip IS NULL OR gip NOT IN (SELECT gip FROM voted))) AS dormant_hidden
FROM open_topics
-- A topic with no GIP number yet (an early draft) is still moving toward one,
-- so only a MATCHED number is excluded.
WHERE (gip IS NULL OR gip NOT IN (SELECT gip FROM voted))
  AND days_idle <= @idle_days
-- phase-2 (pre-vote signalling) before phase-1 (idea stage): DESC on the label
-- sorts '2' above '1' and is stable if a phase-3 is ever added to the filter.
ORDER BY phase DESC, last_posted_at DESC, topic_id
LIMIT @cap
