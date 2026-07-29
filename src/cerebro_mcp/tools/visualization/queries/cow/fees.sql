WITH @tmx
SELECT u.chain_id AS chain_id, u.token AS token,
       if(tm.token='','',tm.symbol) AS token_symbol,
       u.policy_raw AS policy_raw,
       multiIf(positionCaseInsensitive(u.policy_raw,'priceImprovement')>0,'price_improvement',
               positionCaseInsensitive(u.policy_raw,'surplus')>0,'surplus',
               positionCaseInsensitive(u.policy_raw,'volume')>0,'volume','other') AS policy_family,
       u.fee_entries AS fee_entries, u.orders AS orders,
       toString(u.amount_sum) AS amount_raw,
       if(tm.token='',NULL,tm.decimals) AS token_decimals,
       if(tm.token='',NULL,toFloat64(u.amount_sum)/pow(10,toFloat64(tm.decimals))) AS amount,
       u.indexed_from AS indexed_from, u.indexed_to AS indexed_to,
       u.source_observed_at AS source_observed_at
FROM (@fee_union) AS u
LEFT JOIN tmx AS tm ON tm.chain_id=u.chain_id AND tm.token=u.token
ORDER BY u.fee_entries DESC, u.chain_id, u.token, u.policy_raw
