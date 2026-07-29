
WITH tokens AS (
  SELECT
    lower(address) AS token_address,
    symbol,
    decimals,
    date_start,
    date_end
  FROM tokens_whitelist
),
deduped_logs AS (
  SELECT
    concat('0x', transaction_hash) AS transaction_hash,
    concat('0x', lower(address)) AS token_contract,
    topic1,
    topic2,
    data,
    block_timestamp
  FROM execution.logs
  WHERE topic0 = '@erc20_transfer_topic'
    AND block_timestamp >= toStartOfDay(now())
    AND block_timestamp <= now()
),
transfers AS (
  SELECT
    l.transaction_hash,
    l.block_timestamp,
    t.token_address,
    t.symbol,
    t.decimals,
    lower(concat('0x', substring(l.topic1, 25, 40))) AS sender,
    lower(concat('0x', substring(l.topic2, 25, 40))) AS receiver,
    reinterpretAsInt256(reverse(unhex(l.data))) AS value_raw
  FROM deduped_logs l
  INNER JOIN tokens t
    ON lower(l.token_contract) = t.token_address
   AND l.block_timestamp >= t.date_start
   AND (t.date_end IS NULL OR l.block_timestamp < t.date_end)
  WHERE lower(concat('0x', substring(l.topic1, 25, 40))) = {address:String}
     OR lower(concat('0x', substring(l.topic2, 25, 40))) = {address:String}
)
SELECT
  transaction_hash,
  {address:String} AS wallet_address,
  block_timestamp AS timestamp,
  toDate(block_timestamp) AS date,
  CASE
    WHEN sender = {address:String} AND receiver = '0x4822521e6135cd2599199c83ea35179229a172ee'
      THEN 'Payment'
    WHEN receiver = {address:String} AND sender = '0x4822521e6135cd2599199c83ea35179229a172ee'
      THEN 'Reversal'
    WHEN receiver = {address:String} AND sender = '0xcdf50be9061086e2ecfe6e4a1bf9164d43568eec'
      THEN 'Cashback'
    WHEN receiver = {address:String} AND sender = '0x0000000000000000000000000000000000000000'
      THEN 'Fiat Top Up'
    WHEN sender = {address:String} AND receiver = '0x0000000000000000000000000000000000000000'
      THEN 'Fiat Off-ramp'
    WHEN receiver = {address:String}
      THEN 'Crypto Deposit'
    ELSE 'Crypto Withdrawal'
  END AS action,
  symbol,
  CASE
    WHEN sender = {address:String} THEN 'out'
    ELSE 'in'
  END AS direction,
  round(toFloat64(value_raw) / power(10, decimals), 6) AS amount,
  round((toFloat64(value_raw) / power(10, decimals)) * coalesce(p.price, 0), 2) AS amount_usd,
  CASE
    WHEN sender = {address:String} THEN receiver
    ELSE sender
  END AS counterparty
FROM transfers
LEFT JOIN int_execution_token_prices_daily p
  ON p.date = toDate(block_timestamp)
 AND p.symbol = symbol
ORDER BY timestamp DESC
LIMIT 500

