-- Token class for a classified row. A registry token whose hub has no price on
-- that date (e.g. SAFE before its 2024-04 listing) is shown as 'listed' — real,
-- unvalued — never guessed.
multiIf(x_role = 'retired_mirror', 'retired_mirror',
        x_spam != '', 'spam',
        x_role = 'priced' AND x_price IS NOT NULL, 'priced',
        x_role IN ('priced', 'listed'), 'listed',
        'unverified')
