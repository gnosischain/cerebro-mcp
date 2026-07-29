
SELECT {voter:String} AS voter_key, any(voter) AS voter_display,
       count() AS vote_count, sum(vp) AS total_vp, avg(vp) AS avg_vp,
       min(created_at) AS first_vote_at, max(created_at) AS last_vote_at,
       count() / nullIf((SELECT count()
                         FROM governance_db.snapshot_proposals FINAL), 0)
         AS participation_rate,
       (SELECT count() FROM governance_db.snapshot_follows FINAL
        WHERE lower(follower) = {voter:String}) AS follower_row_count
FROM governance_db.snapshot_votes FINAL
WHERE lower(voter) = {voter:String}
ORDER BY voter_key
