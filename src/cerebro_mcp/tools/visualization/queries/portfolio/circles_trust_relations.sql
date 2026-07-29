
SELECT *
FROM api_execution_circles_v2_trust_relations_current
WHERE lower(truster) = {avatar:String}
   OR lower(trustee) = {avatar:String}
ORDER BY valid_from DESC
LIMIT 2000

