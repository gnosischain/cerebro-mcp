
SELECT @cid AS chain_id,t.owner AS trader,
       uniq(@trade_key) AS fill_count,
       uniq(t.tx_hash) AS settlement_transactions,
       uniq(tuple(least(t.sell_token,t.buy_token),greatest(t.sell_token,t.buy_token))) AS distinct_pairs,
       min(t.block_timestamp) AS fs_arm,
       max(t.block_timestamp) AS ls_arm,
       max(t.observed_at) AS obs_arm
FROM cow_db.trades AS t
WHERE @arm_where
GROUP BY trader
