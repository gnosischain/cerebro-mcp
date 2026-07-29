
WITH @token_cte, cand AS (
  SELECT order_uid,
         argMax(status,observed_at) AS status_l,
         argMax(owner,observed_at) AS owner_l,
         argMax(kind,observed_at) AS kind_l,
         argMax(class,observed_at) AS class_l,
         argMax(partially_fillable,observed_at) AS pf_l,
         argMax(sell_token,observed_at) AS st,
         argMax(buy_token,observed_at) AS bt,
         argMax(sell_amount,observed_at) AS sa,
         argMax(buy_amount,observed_at) AS ba,
         argMax(creation_date,observed_at) AS created,
         argMax(valid_to,observed_at) AS vt,
         max(observed_at) AS obs
  FROM cow_db.orders
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND ((sell_token={base:String} AND buy_token={quote:String})
         OR (sell_token={quote:String} AND buy_token={base:String}))
    AND creation_date<=parseDateTime64BestEffort({at_ts:String})
    AND toDateTime(valid_to)>parseDateTime64BestEffort({at_ts:String})
  GROUP BY order_uid
), fills AS (
  SELECT order_uid,sum(fsa) AS filled_sell,sum(fba) AS filled_buy
  FROM (
    SELECT t.order_uid AS order_uid,t.tx_hash,t.log_index,
           argMax(t.sell_amount,t.observed_at) AS fsa,
           argMax(t.buy_amount,t.observed_at) AS fba
    FROM cow_db.trades AS t
    WHERE t.environment={env:String} AND t.chain_id={chain_id:UInt64}
      AND t.order_uid IN (SELECT order_uid FROM cand)
      AND t.block_timestamp IS NOT NULL
      AND t.block_timestamp<=parseDateTime64BestEffort({at_ts:String})
    GROUP BY t.order_uid,t.tx_hash,t.log_index
  ) GROUP BY order_uid
), term AS (
  SELECT order_uid,min(event_timestamp) AS terminated_at
  FROM cow_db.order_events
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND order_uid IN (SELECT order_uid FROM cand)
    AND event_type IN ('OrderInvalidated','OrderInvalidation','status:cancelled','status:fulfilled')
    AND event_timestamp IS NOT NULL
    AND event_timestamp<=parseDateTime64BestEffort({at_ts:String})
  GROUP BY order_uid
), term_any AS (
  -- Unbounded existence check (no at_ts cap): does ANY timestamped terminal
  -- event exist for the order, ever? Backfilled cancelled orders have none —
  -- their cancel TIME is unknowable, so they are excluded from reconstruction
  -- at every T instead of phantom-resting until valid_to. An order cancelled
  -- AFTER T stays in the book at T because it IS here (and not in term).
  SELECT DISTINCT order_uid
  FROM cow_db.order_events
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND order_uid IN (SELECT order_uid FROM cand)
    AND event_type IN ('OrderInvalidated','OrderInvalidation','status:cancelled','status:fulfilled')
    AND event_timestamp IS NOT NULL
), book AS (
  -- Explicit column list: a qualified asterisk (c.*) through this joined CTE
  -- does NOT preserve plain column names on the server (code 47 downstream).
  SELECT c.order_uid AS order_uid,c.owner_l AS owner_l,c.kind_l AS kind_l,
    c.class_l AS class_l,c.pf_l AS pf_l,c.st AS st,c.bt AS bt,
    c.sa AS sa,c.ba AS ba,c.created AS created,c.vt AS vt,c.obs AS obs,
    if(f.filled_sell<c.sa,toUInt256(c.sa-f.filled_sell),toUInt256(0)) AS residual_sell_raw,
    if(f.filled_buy<c.ba,toUInt256(c.ba-f.filled_buy),toUInt256(0)) AS residual_buy_raw,
    if(c.kind_l='buy',
       toFloat64(c.sa)*toFloat64(residual_buy_raw)/nullIf(toFloat64(c.ba),0),
       toFloat64(residual_sell_raw)) AS remaining_sell_float,
    if(c.kind_l='buy',
       toFloat64(residual_buy_raw),
       toFloat64(c.ba)*toFloat64(residual_sell_raw)/nullIf(toFloat64(c.sa),0)) AS remaining_buy_float
  FROM cand AS c
  LEFT JOIN fills AS f ON f.order_uid=c.order_uid
  LEFT JOIN term AS x ON x.order_uid=c.order_uid
  WHERE x.order_uid=''
    AND (c.status_l!='cancelled' OR c.order_uid IN (SELECT order_uid FROM term_any))
), enriched AS (
  SELECT bk.*,
    if(s.token='','',s.symbol) AS sell_symbol,
    if(b.token='','',b.symbol) AS buy_symbol,
    if(s.token='',NULL,s.decimals) AS sell_decimals,
    if(b.token='',NULL,b.decimals) AS buy_decimals,
    if(s.token='',NULL,remaining_sell_float/pow(10,toFloat64(s.decimals))) AS remaining_sell,
    if(b.token='',NULL,remaining_buy_float/pow(10,toFloat64(b.decimals))) AS remaining_buy,
    if(bk.st={base:String},'ask','bid') AS side
  FROM book bk
  LEFT JOIN tm s ON s.token=bk.st
  LEFT JOIN tm b ON b.token=bk.bt
  WHERE remaining_sell_float>0 AND remaining_buy_float>0
), priced AS (
  SELECT order_uid,owner_l AS owner,kind_l AS kind,side,class_l AS order_class,
    pf_l AS partially_fillable,created AS creation_date,vt AS valid_to,
    st AS sell_token,bt AS buy_token,sell_symbol,buy_symbol,
    sell_decimals,buy_decimals,
    if(side='ask',remaining_buy/nullIf(remaining_sell,0),
                  remaining_sell/nullIf(remaining_buy,0)) AS price,
    if(side='ask',remaining_sell,remaining_buy) AS amount_base,
    if(side='ask',remaining_buy,remaining_sell) AS amount_quote,
    toString(sa) AS sell_amount_raw,toString(ba) AS buy_amount_raw,
    obs AS source_observed_at
  FROM enriched
  WHERE sell_decimals IS NOT NULL AND buy_decimals IS NOT NULL
)
@ladder_projection
