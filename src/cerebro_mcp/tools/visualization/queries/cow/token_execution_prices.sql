
WITH @token_metadata_cte, fills AS (
 SELECT t.block_timestamp,t.tx_hash,t.observed_at,
        if(t.sell_token={id:String},t.buy_token,t.sell_token) AS quote_token,
        if(t.sell_token={id:String},qbuy.symbol,qsell.symbol) AS quote_symbol,
        if(t.sell_token={id:String},
           toFloat64(t.sell_amount)/pow(10,toFloat64(base.decimals)),
           toFloat64(t.buy_amount)/pow(10,toFloat64(base.decimals))) AS base_qty,
        if(t.sell_token={id:String},
           toFloat64(t.buy_amount)/pow(10,toFloat64(qbuy.decimals)),
           toFloat64(t.sell_amount)/pow(10,toFloat64(qsell.decimals))) AS quote_qty
 FROM cow_db.trades AS t
 INNER JOIN tm AS base ON base.token={id:String}
 INNER JOIN tm AS qbuy ON qbuy.token=t.buy_token
 INNER JOIN tm AS qsell ON qsell.token=t.sell_token
 PREWHERE (t.sell_token={id:String} OR t.buy_token={id:String})
 WHERE t.environment={env:String} AND t.chain_id={chain_id:UInt64}
  AND t.block_timestamp IS NOT NULL
  AND t.block_number<=(SELECT argMax(block_number,updated_at) FROM cow_db.indexing_checkpoints
                       WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND source='rpc')
)
SELECT toStartOfDay(block_timestamp) AS bucket,quote_token,any(quote_symbol) AS quote_symbol,
       sum(quote_qty)/nullIf(sum(base_qty),0) AS vwap_quote_per_token,
       sum(base_qty) AS base_volume,count() AS fill_count,
       uniq(tx_hash) AS settlement_transactions,min(block_timestamp) AS indexed_from,
       max(block_timestamp) AS indexed_to,max(observed_at) AS source_observed_at
FROM fills
WHERE base_qty>0 AND quote_qty>=0
GROUP BY bucket,quote_token
ORDER BY bucket,quote_token
