-- Latest observed_at on protocol_fees for ONE chain, parenthesised for inline use.
--
-- Fees stand alone on protocol_fees (small, API-enriched). Joining the trades
-- view only supplied block timestamps and was the memory/time hog, so
-- observed_at is the honest basis for API-sourced fee rows — it is when the row
-- was observed, not when the fee was charged on chain, and the two differ.
--
-- @chain_id is substituted, not bound: one statement per chain arm.
(SELECT max(observed_at) FROM cow_db.protocol_fees WHERE environment={env:String} AND chain_id=@chain_id)
