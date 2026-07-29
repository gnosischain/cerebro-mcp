
cc AS (
  SELECT chain_id, count() AS a, maxOrNull(observed_at) AS b
  FROM cow_db.solver_competitions FINAL
  WHERE environment={env:String} AND chain_id IN (@ids)
  GROUP BY chain_id
)
