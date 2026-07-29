
SELECT
  count() AS holdings_count,
  sum(balance_demurraged) AS balance_demurraged
FROM api_execution_circles_v2_avatar_balances_latest
WHERE lower(avatar) = {avatar:String}

