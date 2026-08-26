
WITH topic_gip AS (
  SELECT id AS topic_id, @gip_title AS src_gip
  FROM @gov_db.forum_topics FINAL
  WHERE @gip_title IS NOT NULL
),
cites AS (
  -- Every GIP number appearing in a post body. extractAll + arrayJoin gives one
  -- row per MENTION, so the edge weight counts citations, not posts. The
  -- @gip_pattern here is GIP_MENTION_PATTERN_SQL — deliberately UNANCHORED,
  -- unlike the canonical title-identity pattern the src side uses: a mention
  -- mid-body is exactly what an edge IS. toUInt32OrNull for type symmetry with
  -- the title side.
  SELECT p.topic_id AS topic_id,
         p.created_at AS created_at,
         toUInt32OrNull(arrayJoin(extractAll(p.raw, '@gip_pattern'))) AS dst_gip
  FROM @gov_db.forum_posts AS p FINAL
)
SELECT tg.src_gip AS src_gip,
       ci.dst_gip AS dst_gip,
       count() AS weight,
       uniqExact(ci.topic_id) AS topics,
       -- When the citing happened. An edge is a conversation, and a 2021 arc
       -- means something different from one added last week.
       min(ci.created_at) AS first_mention,
       max(ci.created_at) AS last_mention
FROM cites AS ci
INNER JOIN topic_gip AS tg ON tg.topic_id = ci.topic_id
WHERE ci.dst_gip IS NOT NULL AND ci.dst_gip > 0
  -- A GIP thread citing its own number is not a relationship.
  AND ci.dst_gip != tg.src_gip
GROUP BY src_gip, dst_gip
ORDER BY weight DESC, src_gip, dst_gip
LIMIT @cap
