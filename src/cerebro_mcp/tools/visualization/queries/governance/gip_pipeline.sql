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
),
-- Not yet voted. Applied before every count below so the disclosures describe
-- the same population the list is drawn from.
pending AS (
  SELECT * FROM open_topics
  -- A topic with no GIP number yet is still pending, so only a MATCHED number
  -- is excluded.
  WHERE gip IS NULL OR gip NOT IN (SELECT gip FROM voted)
)
SELECT topic_id, title, gip, phase, posts_count, participant_count, views,
       created_at, last_posted_at, days_idle,
       -- The two exclusion counts PARTITION every pending row this list omits,
       -- so the panel can state each one instead of silently truncating. Every
       -- pending topic is exactly one of: listed (phase-2, inside the window),
       -- an active idea, or dormant. An earlier version scoped `dormant_hidden`
       -- to phase-2, which left a phase-1 topic idle past the window counted in
       -- NEITHER bucket -- excluded and undisclosed, which is the failure mode
       -- the counts exist to prevent.
       --
       -- Measured 2026-07-30: of 157 open phase-1/2 topics the median has been
       -- idle 1,265 days. The pipeline is mostly an archive, and a list that hid
       -- that would imply a pipeline that is moving.
       (SELECT count() FROM pending
        WHERE days_idle > @idle_days) AS dormant_hidden,
       -- phase-1 is the IDEA stage: upstream of a vote rather than moving
       -- toward one. Only the ones still being discussed are worth a count --
       -- a dormant idea is already in `dormant_hidden`.
       (SELECT count() FROM pending
        WHERE phase = 'phase-1' AND days_idle <= @idle_days) AS ideas_hidden
FROM pending
-- phase-2 ONLY. That is the community's pre-vote signalling stage, which is
-- what "moving toward a GIP" means; phase-1 ideas are disclosed as a count.
WHERE phase = 'phase-2'
  AND days_idle <= @idle_days
ORDER BY last_posted_at DESC, topic_id
LIMIT @cap
