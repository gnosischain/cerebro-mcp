-- Bucketed activity across chains: one UNION arm per chain, wrapped and ordered.
--
-- Shared by the overview, trades and traders sections — they differ only in which
-- CTEs they need and what each arm selects, both of which arrive as fragments.
--
-- WHY ARMS AND NOT ONE GROUPED SCAN: expanding the reorg-safe trades view
-- (FINAL + chain_blocks join + checkpoint subquery) for ten chains in a single
-- pass exceeds the server's memory (observed ~11 GiB). One arm per chain keeps
-- each expansion single-chain.
--
-- HISTORY WORTH KNOWING: this file was added in 77927cf ("sql isolated + miniapps
-- update") and then sat ORPHANED — the three call sites kept assembling the same
-- envelope from Python string literals, so the file was never loaded. Nothing
-- caught it because there was no orphan test; there is one now
-- (test_no_shipped_template_is_orphaned).
--
-- The two fragment names are written without an at-sign in this prose on purpose:
-- this is a whole-query file, so its header is NOT stripped before substitution and
-- a token spelled out here would be substituted into the comment.
WITH @shared_ctes
SELECT * FROM (
@activity_union
) ORDER BY bucket,chain_id
