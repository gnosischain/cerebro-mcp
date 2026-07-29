
SELECT count() AS proposal_count,
       countIf(state = 'active') AS active_count,
       countIf(state = 'pending') AS pending_count,
       countIf(state = 'closed') AS closed_count,
       sum(votes_count) AS vote_count,
       avg(votes_count) AS avg_votes,
       quantileExact(0.5)(votes_count) AS median_votes,
       countIf(quorum > 0 AND scores_total >= quorum) AS quorum_met_count,
       countIf(quorum > 0 AND scores_total < quorum) AS quorum_missed_count,
       countIf(quorum <= 0) AS quorum_unspecified_count,
       (SELECT uniqExact(lower(voter)) FROM governance_db.snapshot_votes FINAL
        WHERE proposal_id IN (
          SELECT id FROM governance_db.snapshot_proposals FINAL WHERE @where
        )) AS unique_voters
FROM governance_db.snapshot_proposals FINAL
WHERE @where
ORDER BY proposal_count
