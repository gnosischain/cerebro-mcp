
WITH @remaining_cte
SELECT order_uid,owner,kind,side,status,creation_date,valid_to,sell_token,buy_token,
       sell_symbol,buy_symbol,
       toString(sell_amount) AS sell_amount_raw,toString(buy_amount) AS buy_amount_raw,
       sell_decimals,buy_decimals,sell_amount_normalized,buy_amount_normalized,
       toString(executed_sell_amount) AS executed_sell_amount_raw,
       toString(executed_buy_amount) AS executed_buy_amount_raw,
       toString(residual_sell_raw) AS residual_sell_amount_raw,
       toString(residual_buy_raw) AS residual_buy_amount_raw,
       remaining_sell,remaining_buy,limit_price,observed_at AS source_observed_at
FROM enriched
ORDER BY creation_date DESC,order_uid DESC
