-- As-of base rows in the shared x_ column contract. Windows run over ALL wallets
-- of the chain, before any wallet or token filter a caller applies, so the spam
-- classifier (mass airdrop) and supply shares mean the same thing on every page.
abase AS (
  SELECT p.ps_chain AS x_chain, p.ps_token AS x_token, p.ps_wallet AS x_wallet,
         p.ps_raw AS x_raw, k.pk_date AS x_date, toStartOfMonth(k.pk_as_of) AS x_bucket,
         k.pk_as_of AS x_as_of, k.pk_status AS x_status, k.pk_block AS x_block,
         k.pk_universe AS x_universe, k.pk_published AS x_published,
         k.pk_served AS x_served, s.su_supply AS x_supply,
         count() OVER (PARTITION BY p.ps_chain, p.ps_token) AS x_wallets,
         uniqExact(p.ps_wallet) OVER (PARTITION BY p.ps_chain) AS x_active,
         sum(p.ps_raw) OVER (PARTITION BY p.ps_chain, p.ps_token) AS x_tok_raw
  FROM apos AS p
  INNER JOIN picked AS k ON k.pk_chain = p.ps_chain AND k.pk_token = p.ps_token
  LEFT JOIN supply AS s
         ON s.su_chain = p.ps_chain AND s.su_token = p.ps_token
        AND s.su_attempt = k.pk_attempt
)
