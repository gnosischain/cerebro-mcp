
SELECT @quorum_status_sql AS quorum_status,
       count() AS proposal_count,
       avg(@quorum_ratio_sql) AS avg_quorum_ratio
FROM governance_db.snapshot_proposals FINAL
WHERE @overlap
GROUP BY quorum_status
ORDER BY proposal_count DESC, quorum_status
