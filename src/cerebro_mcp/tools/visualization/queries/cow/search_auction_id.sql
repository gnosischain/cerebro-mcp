-- Search probe: does this integer exist as an auction id, and on which chains?
--
-- FINAL is required — solver_competitions is a raw ReplacingMergeTree that does
-- not dedup on read, and a competition is re-inserted as it is observed. Without
-- it the evidence_count double-counts versions.
SELECT chain_id,'auction' AS entity_type,'auction' AS role,count() AS evidence_count
FROM cow_db.solver_competitions FINAL WHERE @where AND auction_id={auction_id:UInt64}
GROUP BY chain_id ORDER BY chain_id
