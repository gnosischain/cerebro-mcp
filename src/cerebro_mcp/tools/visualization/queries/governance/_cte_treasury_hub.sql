-- Daily USD prices from the dbt price hub, restricted to the registry's symbols.
-- Keyed by (upper(symbol), date); the hub only publishes days before today().
hubp AS (
  SELECT upper(symbol) AS h_sym, date AS h_date, price AS h_price
  FROM @hub_table
  WHERE upper(symbol) IN {hub_syms:Array(String)}
)
