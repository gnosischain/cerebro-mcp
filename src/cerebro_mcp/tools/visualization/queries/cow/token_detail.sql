
WITH @token_metadata_cte
SELECT token,symbol,name,decimals,
       if(token='@native_token','synthetic_native','token_metadata') AS source,
       metadata_observed_at AS source_observed_at
FROM tm
WHERE token={id:String}
ORDER BY token
