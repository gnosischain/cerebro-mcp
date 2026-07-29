
SELECT
  block_timestamp,
  CASE
    WHEN topic0 = '@added_owner_topic' THEN 'added_owner'
    WHEN topic0 = '@removed_owner_topic' THEN 'removed_owner'
    ELSE 'changed_threshold'
  END AS event_kind,
  lower(concat('0x', substring(data, 25, 40))) AS owner,
  toUInt32(reinterpretAsUInt256(reverse(unhex(data)))) AS threshold
FROM execution.logs
WHERE lower(address) = replaceAll({address:String}, '0x', '')
  AND topic0 IN (
    '@added_owner_topic',
    '@removed_owner_topic',
    '@changed_threshold_topic'
  )
  AND block_timestamp >= toStartOfDay(now())
  AND block_timestamp <= now()
ORDER BY block_timestamp ASC
LIMIT 200

