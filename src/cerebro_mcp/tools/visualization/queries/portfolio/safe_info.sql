
SELECT
  safe_address,
  creation_version,
  block_date,
  block_timestamp
FROM int_execution_safes
WHERE lower(safe_address) = {address:String}
LIMIT 1

