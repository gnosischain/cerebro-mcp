WITH @shared_ctes,@firsts_cte
SELECT * FROM (
@activity_union
) ORDER BY bucket,chain_id
