
SELECT @cid AS chain_id,least(t.sell_token,t.buy_token) AS token0,
       greatest(t.sell_token,t.buy_token) AS token1,
       uniq(@trade_key) AS fill_count,uniq(t.tx_hash) AS settlement_transactions,
       min(t.block_timestamp) AS indexed_from,max(t.block_timestamp) AS indexed_to,
       max(t.observed_at) AS source_observed_at
FROM cow_db.trades AS t
WHERE @arm_where
GROUP BY token0,token1
