
SELECT type, count() AS proposal_count, sum(votes_count) AS vote_count,
       sum(scores_total) AS total_vp
FROM governance_db.snapshot_proposals FINAL
WHERE @overlap
GROUP BY type
ORDER BY proposal_count DESC, type
