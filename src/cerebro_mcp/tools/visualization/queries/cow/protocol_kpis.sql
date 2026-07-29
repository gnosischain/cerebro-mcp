WITH @shared_ctes@np_cte
SELECT * FROM (
  SELECT t.chain_id AS chain_id,@kpi_body
  GROUP BY t.chain_id
UNION ALL
  SELECT toUInt64(0) AS chain_id,@kpi_body
) ORDER BY chain_id
