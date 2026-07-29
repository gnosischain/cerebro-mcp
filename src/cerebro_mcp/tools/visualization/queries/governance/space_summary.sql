
SELECT
  (SELECT count() FROM governance_db.snapshot_proposals FINAL WHERE @overlap) AS proposal_count,
  (SELECT count() FROM governance_db.snapshot_votes FINAL WHERE @votes_time) AS vote_count,
  (SELECT uniqExact(lower(voter)) FROM governance_db.snapshot_votes FINAL WHERE @votes_time) AS voter_count,
  (SELECT count() FROM governance_db.snapshot_follows FINAL WHERE @follows_time) AS follower_count,
  (SELECT count() FROM governance_db.forum_topics FINAL WHERE @topics_time) AS topic_count,
  (SELECT count() FROM governance_db.forum_posts FINAL WHERE @posts_time) AS post_count,
  (SELECT count() FROM governance_db.forum_users FINAL) AS forum_user_count
ORDER BY proposal_count
