
SELECT * FROM (
  SELECT 'settlement_executor' AS role,tx_hash AS identifier,
         toNullable(block_timestamp) AS event_time,observed_at AS source_observed_at
  FROM cow_db.settlements
  PREWHERE solver={id:String}
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
  ORDER BY block_timestamp DESC
  LIMIT @row_cap
  UNION ALL
  SELECT 'competition_solver',toString(auction_id),CAST(NULL AS Nullable(DateTime64(3))),
         observed_at
  FROM cow_db.competition_solutions FINAL
  WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND solver={id:String}
)
ORDER BY event_time DESC,identifier DESC
LIMIT @row_cap
