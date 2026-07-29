
tmx AS (
    SELECT chain_id, token,
           argMax(symbol, observed_at) AS symbol,
           argMax(name, observed_at) AS name,
           argMax(decimals, observed_at) AS decimals,
           max(observed_at) AS metadata_observed_at
    FROM cow_db.token_metadata
    WHERE environment={env:String} AND chain_id IN (@ids)
    GROUP BY chain_id, token
    UNION ALL
    SELECT nt.1 AS chain_id,'@native_token' AS token,nt.2 AS symbol,
           nt.2 AS name,toUInt8(18) AS decimals,
           toDateTime64(0,3,'UTC') AS metadata_observed_at
    FROM (SELECT arrayJoin([@native_tuples]) AS nt)
)
