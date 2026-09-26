-- Is a candidate day COMPLETE? Its served pool count must reach @ratio of the
-- GREATER of (a) the pools published that day — a shortfall is an eligibility
-- failure: published, not served — and (b) the most any of the previous
-- @peak_days candidates served — a shortfall is a run still in progress or one
-- that stopped part-way, where raw and served are equally short.
--
-- (b) looks BACK only. A peak over the whole candidate window would pin the as-of
-- to the last day before a genuine universe shrink (pools disabled in the
-- registry) until that day aged out; a trailing frame bounds the lag to
-- @peak_days. On the first candidate the frame is empty and max() is 0, so (a)
-- alone decides. The 7-day trailing peak mirrors missing_days.sql's gap rule.
@served >= @ratio * greatest(@published,
  max(@served) OVER (ORDER BY @day ROWS BETWEEN @peak_days PRECEDING AND 1 PRECEDING))
