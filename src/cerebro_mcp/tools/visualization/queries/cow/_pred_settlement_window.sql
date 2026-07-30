-- Relative-window predicate on settlements, taking the anchor as a fragment.
--
-- Separate from _anchor_settlements.sql because this is the PREDICATE and that is
-- the ANCHOR: the same anchor is used bare by other callers. @anchor is rendered
-- from _anchor_settlements.sql with the caller's scope.
--
-- `block_timestamp IS NOT NULL` is not redundant: BNB settlements carry NULL
-- timestamps, and a NULL would silently drop out of the >= comparison rather
-- than being visibly excluded.
block_timestamp IS NOT NULL AND block_timestamp >= (@anchor) - toIntervalDay({window_days:UInt32})
