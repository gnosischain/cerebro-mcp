-- The reviewed token registry (tools/visualization/treasury_registry.py), bound as
-- parallel arrays. The ONLY source of USD identity: a token is priced through its
-- registry price symbol, never through its on-chain symbol (spoofs copy symbols).
reg AS (
  SELECT tupleElement(r, 1) AS reg_chain, tupleElement(r, 2) AS reg_token,
         tupleElement(r, 3) AS reg_role, tupleElement(r, 4) AS reg_psym,
         tupleElement(r, 5) AS reg_basis, tupleElement(r, 6) AS reg_symbol,
         tupleElement(r, 7) AS reg_dec, tupleElement(r, 8) AS reg_asset,
         tupleElement(r, 9) AS reg_class,
         toDate(tupleElement(r, 10)) AS reg_from, toDate(tupleElement(r, 11)) AS reg_to
  FROM (
    SELECT arrayJoin(arrayZip(
      {reg_chain:Array(UInt64)}, {reg_token:Array(String)}, {reg_role:Array(String)},
      {reg_psym:Array(String)}, {reg_basis:Array(String)}, {reg_symbol:Array(String)},
      {reg_dec:Array(UInt8)}, {reg_asset:Array(String)}, {reg_class:Array(String)},
      {reg_from:Array(String)}, {reg_to:Array(String)})) AS r
  )
)
