-- Fee tier label. Uniswap v3 pools carry one of four static tiers; Algebra
-- pools set a dynamic fee that has taken 435 distinct values on this chain, so
-- the raw pips are bucketed rather than shown as a category. Balancer pools
-- have no tick-level fee on this plane at all, hence the NULL branch.
multiIf(@alias IS NULL, CAST(NULL AS Nullable(String)),
        @alias <= 100, '<= 0.01%',
        @alias <= 500, '0.05%',
        @alias <= 3000, '0.30%',
        @alias <= 10000, '1.00%',
        '> 1%')
