
SELECT order_uid,owner,kind,side,order_class,partially_fillable,
       creation_date,valid_to,sell_token,buy_token,sell_symbol,buy_symbol,
       sell_decimals,buy_decimals,price,amount_base,amount_quote,
       sell_amount_raw,buy_amount_raw,
       creation_date AS indexed_from,creation_date AS indexed_to,
       source_observed_at
FROM priced
WHERE isFinite(price) AND price>0
ORDER BY side,price,order_uid
