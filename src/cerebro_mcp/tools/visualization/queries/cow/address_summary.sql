
SELECT
 (SELECT count() FROM cow_db.trades PREWHERE owner={id:String} WHERE environment={env:String} AND chain_id={chain_id:UInt64}) AS owned_fills,
 (SELECT count() FROM cow_db.orders FINAL WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND owner={id:String}) AS owned_orders,
 (SELECT count() FROM cow_db.settlements PREWHERE solver={id:String} WHERE environment={env:String} AND chain_id={chain_id:UInt64}) AS executed_settlements,
 (SELECT count() FROM cow_db.competition_solutions FINAL WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND solver={id:String}) AS submitted_solutions,
 (SELECT max(observed_at) FROM cow_db.orders FINAL WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND owner={id:String}) AS source_observed_at
ORDER BY owned_fills
