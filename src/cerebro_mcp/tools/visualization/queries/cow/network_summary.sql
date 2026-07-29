WITH @shared_ctes,@order_anchor_cte,@competitions_cte,@trades_cte,@orders_cte
SELECT spine.chain_id AS chain_id,coalesce(tr.a,0) AS trade_count,coalesce(tr.b,0) AS settlement_transactions,coalesce(og.a,0) AS order_count,coalesce(ogopen.b,0) AS observed_open_orders,coalesce(cc.a,0) AS competition_count_all_indexed,tr.c AS indexed_from,tr.d AS indexed_to,tr.e AS source_observed_at,og.c AS order_indexed_from,og.d AS order_indexed_to,og.e AS order_observed_at,cc.b AS competition_observed_at
FROM (SELECT arrayJoin([@ids]) AS chain_id) AS spine
LEFT JOIN tr ON tr.chain_id=spine.chain_id
LEFT JOIN og ON og.chain_id=spine.chain_id
LEFT JOIN ogopen ON ogopen.chain_id=spine.chain_id
LEFT JOIN cc ON cc.chain_id=spine.chain_id
ORDER BY spine.chain_id
