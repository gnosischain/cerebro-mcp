
SELECT 'proposal' AS entity_type, id AS identifier, title AS label,
       'proposal' AS role, toInt64(votes_count) AS evidence_count,
       0 AS match_rank
FROM governance_db.snapshot_proposals FINAL
WHERE id = {q:String}
ORDER BY identifier
