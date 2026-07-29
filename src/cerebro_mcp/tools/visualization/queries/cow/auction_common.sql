FROM cow_db.solver_competitions AS c FINAL
LEFT JOIN blk AS b ON b.chain_id=c.chain_id AND b.block_number=c.auction_block
WHERE @scope_pred_c AND b.block_number!=0 AND @time_pred
