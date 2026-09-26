-- Valuation + classification. The hub is joined on the REGISTRY price symbol
-- (x_psym), never on the on-chain symbol — the security invariant of this plane,
-- test-pinned. ASOF on-or-before the row's date, bounded by a maximum price age
-- so a frozen feed never values a position silently. Registry decimals override
-- metadata decimals; unknown decimals leave units NULL (never scaled wrong).
classified AS (
  SELECT q.*,
         if(q.x_psym != '' AND h.h_date > toDate(0)
            AND dateDiff('day', h.h_date, q.x_date) <= @max_price_age,
            toNullable(h.h_price), NULL) AS x_price,
         if(x_price IS NULL, NULL, toNullable(h.h_date)) AS x_price_day,
         if(q.x_reg_dec >= 0, toNullable(toUInt8(q.x_reg_dec)), q.x_meta_dec) AS x_dec,
         if(x_dec IS NULL, NULL, toFloat64(q.x_raw) / pow(10, x_dec)) AS x_units,
         @spam_expr AS x_spam,
         @class_expr AS x_class,
         if(x_class = 'priced', x_units * x_price, NULL) AS x_value,
         x_class IN ('priced', 'listed', 'unverified') AS x_visible,
         q.x_reg_asset = 'GNO' AS x_is_gno
  FROM enriched AS q
  ASOF LEFT JOIN hubp AS h ON h.h_sym = q.x_psym AND q.x_date >= h.h_date
)
