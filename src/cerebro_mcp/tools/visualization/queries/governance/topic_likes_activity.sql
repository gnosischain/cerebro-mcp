-- One topic's likes over time at (bucket, post) grain — the topic drill-down
-- stacks these per post (top posts labeled by post number, the rest folded
-- into a COUNTED "Other posts" series client-side; no author names here —
-- WL-039 privacy alignment keeps names on verbatim-post surfaces only).
-- Buckets are ADAPTIVE:
-- daily while the topic's like history spans <= 120 days (a topic's hot life
-- is usually weeks — weekly buckets flatten it), weekly beyond that. 120
-- mirrors the day/week cliff of the section charts' _bucket() helper. The
-- span CTE is referenced twice as a scalar subquery — a conscious exception
-- to the once-only rule, over one topic's likes (hundreds of rows at most).
-- ACTIVE eligibility applies (hidden = 0 AND deleted = 0). The posts join is
-- LEFT on purpose: a like whose post left the index keeps its bucket with
-- post_number 0 and renders as "Unknown post" — a silently vanished like
-- would violate the visible-exclusion rule.
WITH span AS (
  SELECT dateDiff('day', min(created_at), max(created_at)) AS days
  FROM governance_db.forum_likes FINAL
  WHERE hidden = 0 AND deleted = 0 AND topic_id = {topic_id:UInt32}
)
SELECT if((SELECT days FROM span) <= 120,
          toDate(l.created_at),
          toStartOfWeek(l.created_at, 1)) AS bucket,
       if((SELECT days FROM span) <= 120, 'day', 'week') AS bucket_unit,
       fp.post_number AS post_number,
       count() AS likes
FROM governance_db.forum_likes AS l FINAL
LEFT JOIN governance_db.forum_posts AS fp FINAL ON fp.id = l.post_id
WHERE l.hidden = 0 AND l.deleted = 0 AND l.topic_id = {topic_id:UInt32}
GROUP BY bucket, bucket_unit, post_number
ORDER BY bucket, post_number
