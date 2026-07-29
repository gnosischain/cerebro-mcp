SELECT max(block_timestamp) FROM cow_db.chain_blocks
WHERE @scope_pred_bare
  AND block_number IN (SELECT auction_block FROM cow_db.solver_competitions FINAL WHERE @scope_pred_bare)
