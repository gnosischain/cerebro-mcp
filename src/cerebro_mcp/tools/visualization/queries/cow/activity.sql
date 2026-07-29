WITH @shared_ctes
SELECT * FROM (
@activity_union
) ORDER BY bucket,chain_id
