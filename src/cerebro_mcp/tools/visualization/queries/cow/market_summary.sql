
WITH @token_metadata_cte
SELECT {base:String} AS base_token, {quote:String} AS quote_token,
       (SELECT anyOrNull(symbol) FROM tm WHERE token={base:String}) AS base_symbol,
       (SELECT anyOrNull(symbol) FROM tm WHERE token={quote:String}) AS quote_symbol,
       (SELECT anyOrNull(decimals) FROM tm WHERE token={base:String}) AS base_decimals,
       (SELECT anyOrNull(decimals) FROM tm WHERE token={quote:String}) AS quote_decimals,
       uniq((t.tx_hash,t.log_index,t.order_uid)) AS fill_count,
       uniq(t.tx_hash) AS settlement_transactions,
       min(t.block_timestamp) AS indexed_from, max(t.block_timestamp) AS indexed_to,
       max(t.observed_at) AS source_observed_at
FROM cow_db.trades AS t
WHERE @scope_pred AND @pair_filter
  AND t.block_timestamp IS NOT NULL AND @time_pred
ORDER BY base_token,quote_token
