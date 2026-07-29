
  SELECT uniq(@trade_key) AS fill_count,uniq(t.tx_hash) AS settlement_transactions,
         uniq(t.owner) AS unique_traders,
         uniq(tuple(least(t.sell_token,t.buy_token),greatest(t.sell_token,t.buy_token))) AS unique_pairs,
         @vol_expr,
         minOrNull(t.block_timestamp) AS indexed_from,
         maxOrNull(t.block_timestamp) AS indexed_to,
         max(t.observed_at) AS source_observed_at
  FROM cow_db.trades AS t
  INNER JOIN cp ON cp.chain_id=t.chain_id
@np_join  WHERE @kpi_where
