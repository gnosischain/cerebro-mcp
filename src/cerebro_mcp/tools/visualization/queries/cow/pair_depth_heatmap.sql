
WITH @token_cte,
bounds AS (
  -- Reconstruction floor: min(creation_date), NOT min(observed_at) — the
  -- backfill recovers orders back to ~2021-08, and fills/expiry give their
  -- removal times. Only "all" (and clamping in the first capture days)
  -- actually reaches this floor.
  SELECT now() AS t_now,
         ifNull(
           (SELECT min(creation_date) FROM cow_db.orders
              WHERE environment={env:String} AND chain_id={chain_id:UInt64}),
           now() - INTERVAL 30 DAY) AS cap_start
),
win AS (
  SELECT t_now, cap_start,
         greatest(cap_start,
           multiIf({window:String}='24h', t_now - INTERVAL 24 HOUR,
                   {window:String}='7d',  t_now - INTERVAL 7 DAY,
                   {window:String}='30d', t_now - INTERVAL 30 DAY,
                   {window:String}='90d', t_now - INTERVAL 90 DAY,
                   cap_start)) AS w_start
  FROM bounds
),
grid AS (
  SELECT w_start,
         greatest(1, toUInt32(dateDiff('second', w_start, t_now))) AS span_s
  FROM win
),
grid_step AS (
  -- Caller resolution when given, else span/60. Floored at @min_step_label and
  -- coarsened so the grid never exceeds @max_buckets columns — the row budget
  -- is a hard cap, so a too-fine request is honored as far as it fits.
  SELECT w_start, span_s,
         greatest(
           greatest(toUInt32(@min_step_s),
                    if({bucket_seconds:UInt32} > 0,
                       {bucket_seconds:UInt32}, toUInt32(intDiv(span_s, 60)))),
           toUInt32(ceil(span_s / @max_buckets_f))
         ) AS step_s
  FROM grid
),
grid_n AS (
  SELECT w_start, step_s,
         least(@max_buckets, toUInt32(intDiv(span_s, step_s)) + 1) AS n_buckets
  FROM grid_step
),
buckets AS (
  SELECT arrayJoin(arrayMap(i -> w_start + toUInt32(i) * step_s, range(n_buckets))) AS bucket_ts,
         step_s
  FROM grid_n
),
dims AS (
  SELECT (SELECT anyOrNull(decimals) FROM tm WHERE token={base:String}) AS base_dec,
         (SELECT anyOrNull(decimals) FROM tm WHERE token={quote:String}) AS quote_dec
),
cand AS (
  -- valid_to is IMMUTABLE per order_uid and alive_until <= toDateTime(valid_to),
  -- so this prefilter is lossless for the bucket-overlap test while bounding the
  -- argMax hash to orders whose validity reaches the window (the backfilled
  -- orders table holds ~200K rows for a busy pair; ~99% expired long ago).
  SELECT order_uid,
         argMax(status,observed_at) AS status_l,
         argMax(sell_token,observed_at) AS st,
         argMax(sell_amount,observed_at) AS sa,
         argMax(buy_amount,observed_at) AS ba,
         argMax(creation_date,observed_at) AS created,
         argMax(valid_to,observed_at) AS vt
  FROM cow_db.orders
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND ((sell_token={base:String} AND buy_token={quote:String})
         OR (sell_token={quote:String} AND buy_token={base:String}))
    AND toDateTime(valid_to) > (SELECT w_start FROM win)
  GROUP BY order_uid
),
term AS (
  SELECT order_uid, min(event_timestamp) AS terminated_at
  FROM cow_db.order_events
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND order_uid IN (SELECT order_uid FROM cand)
    AND event_type IN ('OrderInvalidated','OrderInvalidation','status:cancelled','status:fulfilled')
    AND event_timestamp IS NOT NULL
  GROUP BY order_uid
),
fill AS (
  -- Order_events does NOT reliably carry a terminal row for every filled order
  -- (most fills are trades, not status events), so without this an order that
  -- was filled but never got a status:fulfilled event would rest forever and
  -- the cross-join explodes. Use the LAST fill as the completion proxy.
  SELECT order_uid, max(block_timestamp) AS filled_out_ts
  FROM cow_db.trades
  WHERE environment={env:String} AND chain_id={chain_id:UInt64}
    AND order_uid IN (SELECT order_uid FROM cand)
    AND block_timestamp IS NOT NULL
  GROUP BY order_uid
),
priced AS (
  SELECT c.order_uid AS order_uid,
    if(c.st={base:String},'ask','bid') AS side,
    if(c.st={base:String},
       toFloat64(c.sa)/pow(10,toFloat64(d.base_dec)),
       toFloat64(c.ba)/pow(10,toFloat64(d.base_dec))) AS base_amt,
    if(c.st={base:String},
       toFloat64(c.ba)/pow(10,toFloat64(d.quote_dec)),
       toFloat64(c.sa)/pow(10,toFloat64(d.quote_dec))) AS quote_amt,
    c.created AS created,
    -- The book removes an order at the EARLIEST of: expiry, terminal event,
    -- and completing fill. Far-future sentinel keeps never-terminated resting
    -- orders alive across the window.
    least(toDateTime(c.vt),
          ifNull(t.terminated_at, toDateTime('2099-01-01 00:00:00')),
          ifNull(f.filled_out_ts, toDateTime('2099-01-01 00:00:00'))) AS alive_until
  FROM cand AS c
  CROSS JOIN dims AS d
  LEFT JOIN term AS t ON t.order_uid=c.order_uid
  LEFT JOIN fill AS f ON f.order_uid=c.order_uid
  WHERE d.base_dec IS NOT NULL AND d.quote_dec IS NOT NULL
    -- Cancelled orders without a timestamped cancel event (and no fill to
    -- bound removal) have an unknowable resting span — excluding them beats
    -- painting phantom depth until valid_to (backfilled cancel-time gap).
    AND (c.status_l!='cancelled' OR t.terminated_at IS NOT NULL
         OR f.filled_out_ts IS NOT NULL)
),
bmed AS (
  -- Per-bucket reference price, keyed by GRID INDEX (buckets start at w_start,
  -- so an epoch-aligned key would be off by a partial step).
  --
  -- Reads RAW orders, deliberately NOT `priced`: CTEs are inlined, so every
  -- extra reference re-runs the whole cand/argMax + term + fill chain. That
  -- chain measured ~5.6s per materialization, and a second one pushed this
  -- past the 20s interactive budget (live TIMEOUT_EXCEEDED). Skipping the
  -- dedup is exact here, not a shortcut: sell_token/sell_amount/buy_amount/
  -- creation_date/valid_to are all IMMUTABLE per order_uid, so duplicate
  -- versions carry identical prices and cannot move a median. Removal logic
  -- (fills, cancels) is irrelevant to "what was this pair worth then".
  SELECT greatest(toInt64(0),
           intDiv(toInt64(dateDiff('second', (SELECT w_start FROM grid_n), o.creation_date)),
                  toInt64((SELECT step_s FROM grid_n)))) AS b_idx,
         quantile(0.5)(if(o.sell_token={base:String},
           toFloat64(o.buy_amount)/pow(10,toFloat64(d.quote_dec))
             /nullIf(toFloat64(o.sell_amount)/pow(10,toFloat64(d.base_dec)),0),
           toFloat64(o.sell_amount)/pow(10,toFloat64(d.quote_dec))
             /nullIf(toFloat64(o.buy_amount)/pow(10,toFloat64(d.base_dec)),0))) AS b_med
  FROM cow_db.orders AS o
  CROSS JOIN dims AS d
  WHERE o.environment={env:String} AND o.chain_id={chain_id:UInt64}
    AND ((o.sell_token={base:String} AND o.buy_token={quote:String})
         OR (o.sell_token={quote:String} AND o.buy_token={base:String}))
    AND toDateTime(o.valid_to) > (SELECT w_start FROM grid_n)
  GROUP BY b_idx
),
pmed AS (
  -- Fallback reference for quiet buckets, taken from `bmed` (<=120 rows) and
  -- NOT from `priced`: CTEs are inlined, so a third `priced` reference would
  -- re-run the cand/term/fill chain a third time (live-measured 14.7s vs 5s).
  SELECT quantile(0.5)(b_med) AS p_med FROM bmed
)
SELECT bucket, bucket_mid, rel_pct, side,
       sum(w) AS depth_base, count() AS orders,
       any(bucket_seconds) AS bucket_seconds,
       any(bucket_ts) AS indexed_from, any(bucket_ts) AS indexed_to
FROM (
  SELECT bucket, bucket_ts, bucket_seconds, bucket_mid, side, w,
         round((price / bucket_mid - 1) * @rel_bin_scale)
           / @rel_bin_step AS rel_pct
  FROM (
    SELECT formatDateTime(b.bucket_ts, '%Y-%m-%dT%H:%i:%SZ') AS bucket,
           b.bucket_ts AS bucket_ts,
           b.step_s AS bucket_seconds,
           -- Quiet buckets (no order created in them) fall back to the window
           -- median; they are low-depth by definition, and the client also
           -- forward-fills bucket_mid for the axis labels.
           coalesce(m.b_med, (SELECT p_med FROM pmed)) AS bucket_mid,
           p.quote_amt / nullIf(p.base_amt, 0) AS price,
           p.side AS side,
           -- Time-weighted: size x the fraction of the bucket the order rested.
           p.base_amt * dateDiff('second',
             greatest(toDateTime(p.created), toDateTime(b.bucket_ts)),
             least(toDateTime(p.alive_until), toDateTime(b.bucket_ts) + b.step_s))
             / b.step_s AS w
    FROM buckets AS b
    CROSS JOIN priced AS p
    LEFT JOIN bmed AS m
      ON m.b_idx = intDiv(
           toInt64(dateDiff('second', (SELECT w_start FROM grid_n), b.bucket_ts)),
           toInt64(b.step_s))
    -- Interval overlap, not point-in-time: CoW books are transient (orders
    -- often rest minutes), so a boundary snapshot would miss most of them.
    WHERE p.created < (b.bucket_ts + b.step_s)
      AND p.alive_until > b.bucket_ts
      AND p.base_amt > 0
  )
  WHERE bucket_mid > 0 AND isFinite(price) AND price > 0
    AND abs(price / bucket_mid - 1) <= @rel_clamp
)
WHERE w > 0
GROUP BY bucket, bucket_mid, rel_pct, side
ORDER BY bucket, side, rel_pct
