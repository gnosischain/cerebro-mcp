-- Pool price in RAW units: token1 atoms per token0 atom, straight from
-- sqrtPriceX96. This is the only price this plane can always state honestly,
-- because it needs no decimals. Converting to human units requires BOTH tokens'
-- decimals and is done by the adjusted-price expression, which returns NULL
-- when either is unknown.
pow(toFloat64(@alias.sqrt_price_x96) / pow(2, 96), 2)
