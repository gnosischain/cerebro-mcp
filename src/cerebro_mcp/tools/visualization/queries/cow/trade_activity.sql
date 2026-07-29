WITH @shared_ctes
SELECT * FROM (
@activity_arms
) ORDER BY bucket,chain_id
