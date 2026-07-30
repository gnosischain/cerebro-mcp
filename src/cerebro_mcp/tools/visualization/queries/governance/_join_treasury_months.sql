-- Restrict a treasury scan to each chain's month-end snapshot dates, and expose
-- `m.bucket`. Joins on BOTH columns: chains publish independently and are months
-- apart, so `snapshot_date` alone would admit one chain's month-end as another's
-- mid-month date and sum two dates into one bucket. Where `m.bucket` is not
-- selected, prefer the cheaper tuple-IN form in `treasury_token_history`.
INNER JOIN months AS m ON t.chain_id = m.chain_id AND t.snapshot_date = m.month_end
