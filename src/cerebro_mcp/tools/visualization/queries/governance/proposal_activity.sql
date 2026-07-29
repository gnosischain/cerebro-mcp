
WITH fp AS (
  SELECT id, start_at FROM governance_db.snapshot_proposals FINAL
  WHERE @where
)
SELECT bucket, metric, metric_value, '@unit' AS bucket_unit
FROM (
  SELECT @start_bucket AS bucket, 'proposals_started' AS metric,
         count() AS metric_value
  FROM fp
  GROUP BY bucket
  UNION ALL
  SELECT @vote_bucket AS bucket, 'votes_cast', count()
  FROM governance_db.snapshot_votes FINAL
  WHERE proposal_id IN (SELECT id FROM fp)
  GROUP BY bucket
)
ORDER BY bucket, metric
