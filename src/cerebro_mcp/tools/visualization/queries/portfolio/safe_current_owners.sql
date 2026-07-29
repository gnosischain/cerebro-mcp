
SELECT
  safe_address,
  owner,
  became_owner_at,
  current_threshold
FROM int_execution_safes_current_owners
WHERE lower(safe_address) = {address:String}
ORDER BY became_owner_at ASC, owner ASC
LIMIT 200

