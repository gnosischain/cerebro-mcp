-- Search probe: which (chain, token) addresses carry this symbol?
--
-- Returns `identifier` because a symbol is NOT an identity — several addresses
-- can claim the same symbol, including deliberate spoofs, so the caller resolves
-- to an address per chain rather than assuming one match.
--
-- FINAL is required: token_metadata is a raw ReplacingMergeTree.
--
-- KNOWN DEBT: `lower(symbol)` wraps the COLUMN, which defeats any index on it.
-- The repo's own rule is to lowercase the PARAMETER instead. Left as-is here
-- because symbols are not stored case-normalised, so lowering only the parameter
-- would change which rows match — a behaviour change, not a refactor. Fixing it
-- needs a normalised column upstream.
SELECT chain_id,token AS identifier,'token' AS entity_type,'token_symbol' AS role,count() AS evidence_count
FROM cow_db.token_metadata FINAL
WHERE @where AND lower(symbol)=lower({symbol:String})
GROUP BY chain_id,token ORDER BY chain_id,token
