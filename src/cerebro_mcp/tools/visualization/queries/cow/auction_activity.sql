
WITH @blk_cte
SELECT toStartOfDay(b.block_timestamp) AS bucket,c.chain_id AS chain_id,
       count() AS competition_count,
       uniqExact(c.winner) AS winners,min(b.block_timestamp) AS indexed_from,
       max(b.block_timestamp) AS indexed_to,max(c.observed_at) AS source_observed_at
@common
GROUP BY bucket,chain_id
ORDER BY bucket,chain_id
