
SELECT
  safe_address,
  owner,
  became_owner_at,
  current_threshold
FROM int_execution_safes_current_owners
WHERE lower(owner) = {address:String}
ORDER BY became_owner_at ASC, safe_address ASC
LIMIT 200

