
SELECT
  address,
  activation_date,
  creation_time
FROM int_execution_gpay_wallets
WHERE lower(address) = {address:String}
LIMIT 1

