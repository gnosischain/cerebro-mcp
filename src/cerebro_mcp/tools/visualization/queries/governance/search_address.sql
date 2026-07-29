
SELECT entity_type, identifier, label, role, evidence_count, match_rank
FROM (
  SELECT 'voter' AS entity_type, {q:String} AS identifier,
         any(voter) AS label, 'voter' AS role,
         toInt64(count()) AS evidence_count, 0 AS match_rank
  FROM governance_db.snapshot_votes FINAL
  WHERE lower(voter) = {q:String}
  HAVING count() > 0
  UNION ALL
  SELECT 'voter', {q:String}, any(follower), 'follower', toInt64(count()), 0
  FROM governance_db.snapshot_follows FINAL
  WHERE lower(follower) = {q:String}
  HAVING count() > 0
)
ORDER BY role
