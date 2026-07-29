
WITH @accounting_ctes
SELECT f.tx_hash AS tx_hash, any(f.block_timestamp) AS block_timestamp,
       count() AS tokens_touched,
       countIf(toFloat64(pr.price)=0) AS unpriced_tokens,
       sum(if(toFloat64(pr.price)>0,
              toFloat64(f.net_atoms)*toFloat64(pr.price)/1e18, 0)) AS net_native_wei_known,
       max(f.block_timestamp) AS source_observed_at
FROM flows AS f
LEFT JOIN am ON am.tx_hash=f.tx_hash
LEFT JOIN pr ON pr.auction_id=am.auction_id AND pr.token=f.token
GROUP BY f.tx_hash
ORDER BY block_timestamp DESC, tx_hash
LIMIT 500
