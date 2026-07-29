
tm AS (
    SELECT token,
           argMax(symbol, observed_at) AS symbol,
           argMax(name, observed_at) AS name,
           argMax(decimals, observed_at) AS decimals,
           max(observed_at) AS metadata_observed_at
    FROM cow_db.token_metadata
    WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    GROUP BY token
    UNION ALL
    SELECT '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
           {native_symbol:String}, {native_symbol:String}, toUInt8(18),
           toDateTime64(0, 3, 'UTC')
)
