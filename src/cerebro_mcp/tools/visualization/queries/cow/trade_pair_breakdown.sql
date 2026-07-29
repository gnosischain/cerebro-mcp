WITH @shared_ctes,@tmx
SELECT p.chain_id AS chain_id, p.token0 AS token0, p.token1 AS token1,
       if(m0.token='','',m0.symbol) AS token0_symbol,
       if(m1.token='','',m1.symbol) AS token1_symbol,
       p.fill_count AS fill_count, p.settlement_transactions AS settlement_transactions,
       p.indexed_from AS indexed_from, p.indexed_to AS indexed_to,
       p.source_observed_at AS source_observed_at
FROM (@pair_union) AS p
LEFT JOIN tmx AS m0 ON m0.chain_id=p.chain_id AND m0.token=p.token0
LEFT JOIN tmx AS m1 ON m1.chain_id=p.chain_id AND m1.token=p.token1
ORDER BY p.fill_count DESC,p.chain_id,p.token0,p.token1
LIMIT 500
