-- Decimals-adjusted price, or NULL. The guard is explicit rather than relying
-- on Nullable arithmetic so that the rule is visible in the SQL the UI shows: a
-- pool whose token decimals were never observed on chain gets NO adjusted
-- price, never a plausible-looking wrong one. Most pools on this plane are in
-- exactly that position.
if(@dec0 IS NULL OR @dec1 IS NULL, NULL,
   @price_raw * pow(10, toInt16(@dec0) - toInt16(@dec1)))
