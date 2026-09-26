-- Daily hub price of one registry-priced token over its registry windows, from
-- the token's first census publication. Empty for tokens the registry does not
-- price — the UI states that rather than drawing a guessed series.
WITH @registry,
@hub
SELECT toString(h.h_date) AS day, h.h_sym AS price_symbol, h.h_price AS price_usd,
       r.reg_role AS role
FROM reg AS r
INNER JOIN hubp AS h ON h.h_sym = r.reg_psym
WHERE r.reg_chain = @chain AND r.reg_token = {addr:String} AND r.reg_role = 'priced'
  AND h.h_date >= r.reg_from AND h.h_date < r.reg_to
  AND h.h_date >= (
    SELECT min(snapshot_date) FROM @pub
    WHERE job_name = '@job' AND target_kind = 'token' AND chain_id = @chain
      AND target_address = {addr:String})
ORDER BY day
