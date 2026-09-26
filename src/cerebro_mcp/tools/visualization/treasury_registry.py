"""Reviewed treasury token registry, wallet labels and spam classifier constants.

DATA ONLY — no SQL lives here (Rule 0). The governance treasury plane binds these
values into its ``queries/governance`` fragments as ClickHouse array parameters.

Why a registry at all
---------------------
Token metadata cannot tell a real token from a fake one. On 2026-09-25 the Ethereum
treasury wallets held 18 distinct contracts whose on-chain ``symbol()`` is ``USDC``
and whose ``name()`` is ``USD Coin`` — one is Circle's, 17 are airdropped fakes with
10^11-scale balances. A "COW" contract (``0x289d…``) reports the DAO holding 94% of its
supply. Any valuation keyed on the on-chain symbol would price all of them. So USD
is only ever assigned through THIS reviewed (chain, address) -> hub-symbol map, and
the on-chain symbol never reaches a price join (test-pinned).

Roles
-----
``priced``          real token with a price series in ``dbt.int_execution_token_prices_daily``
                    (joined on ``price_symbol``, the hub's upper-cased symbol).
``listed``          reviewed real token with NO hub price: shown, never spam, eligible
                    for the CoinGecko spot fallback (today only, never in history).
``retired_mirror``  a contract that mirrors another's ledger (Monerium EURe/GBPe v1 after
                    the 2024-08-25 migration: v1 and v2 report the SAME balances, so
                    summing both double-counts). Shown, never valued, never spam.

Validity windows are ``[valid_from, valid_to)``. One address may have several
non-overlapping entries (EURe v1: priced until 2024-08-25, retired_mirror after).

``price_basis`` is ``direct`` (the hub series IS this token) or ``proxy`` (priced
with a pegged asset's series, e.g. a 1:1 wrapper). Proxies are disclosed in the UI.

Adding a token: verify the canonical address from the issuer / chain explorer (never
from the treasury's own metadata), pick the hub symbol from
``SELECT DISTINCT symbol FROM dbt.int_execution_token_prices_daily`` and run
``tests/test_treasury_registry.py`` (invariants) plus the opt-in live smoke, which
cross-checks decimals, whitelist windows and hub coverage.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

Role = Literal["priced", "listed", "retired_mirror"]

#: Open-ended window ends. 2149-06-06 is ClickHouse ``Date``'s maximum.
MIN_DATE = "1970-01-01"
MAX_DATE = "2149-06-06"
#: "Now" for lookups that have no date: inside every open-ended window.
PRESENT = "2100-01-01"

#: Monerium's v1 -> v2 migration day on Gnosis Chain (dbt.tokens_whitelist windows).
MONERIUM_MIGRATION = "2024-08-25"


@dataclass(frozen=True)
class TokenEntry:
    chain_id: int
    address: str
    role: Role
    #: Trusted display symbol (never the on-chain one).
    symbol: str
    decimals: int
    #: Identity of the SAME asset across chains/representations (UI merge key).
    asset_key: str
    asset_class: str
    #: Upper-cased hub symbol; empty unless role == "priced".
    price_symbol: str = ""
    price_basis: Literal["direct", "proxy", ""] = ""
    valid_from: str = MIN_DATE
    valid_to: str = MAX_DATE
    note: str = ""


def _p(chain: int, address: str, symbol: str, decimals: int, asset_key: str,
       asset_class: str, price_symbol: str, *, basis: str = "direct",
       valid_from: str = MIN_DATE, valid_to: str = MAX_DATE, note: str = "") -> TokenEntry:
    return TokenEntry(chain, address, "priced", symbol, decimals, asset_key, asset_class,
                      price_symbol.upper(), basis, valid_from, valid_to, note)  # type: ignore[arg-type]


def _l(chain: int, address: str, symbol: str, decimals: int, asset_key: str,
       asset_class: str, *, valid_from: str = MIN_DATE, valid_to: str = MAX_DATE,
       note: str = "") -> TokenEntry:
    return TokenEntry(chain, address, "listed", symbol, decimals, asset_key, asset_class,
                      "", "", valid_from, valid_to, note)


def _r(chain: int, address: str, symbol: str, decimals: int, asset_key: str,
       asset_class: str, *, valid_from: str, note: str) -> TokenEntry:
    return TokenEntry(chain, address, "retired_mirror", symbol, decimals, asset_key,
                      asset_class, "", "", valid_from, MAX_DATE, note)


ETH, GNOC, STABLE, BTC, RWA, OTHER = "ETH", "GNO", "Stablecoins", "BTC", "RWA", "Other"

#: Reviewed entries. Addresses verified against issuer docs / dbt.tokens_whitelist
#: (Gnosis Chain) and the treasury census history (every token held at a month-end
#: since 2020, pulled 2026-09-25). Order is irrelevant; (chain, address, window) unique.
TOKENS: tuple[TokenEntry, ...] = (
    # --- Ethereum (chain 1): hub-priced -------------------------------------------
    _p(1, "0x6810e776880c02933d47db1b9fc05908e5386b96", "GNO", 18, "GNO", GNOC, "GNO"),
    _p(1, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6, "USDC", STABLE, "USDC"),
    _p(1, "0xdac17f958d2ee523a2206206994597c13d831ec7", "USDT", 6, "USDT", STABLE, "USDT"),
    _p(1, "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH", 18, "WETH", ETH, "WETH"),
    _p(1, "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0", "wstETH", 18, "wstETH", ETH, "WSTETH"),
    _p(1, "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", "WBTC", 8, "WBTC", BTC, "WBTC"),
    _p(1, "0xdef1ca1fb7fbcdc777520aa7f396b4e015f497ab", "COW", 18, "COW", OTHER, "COW"),
    _p(1, "0x5afe3855358e112b5647b952709e6165e1c1eeee", "SAFE", 18, "SAFE", OTHER, "SAFE"),
    _p(1, "0xf5581dfefd8fb0e4aec526be659cfab1f8c781da", "HOPR", 18, "HOPR", OTHER, "HOPR"),
    _p(1, "0xba100000625a3754423978a60c9317c58a424e3d", "BAL", 18, "BAL", OTHER, "BAL"),
    _p(1, "0xc0c293ce456ff0ed870add98a0828dd4d2903dbf", "AURA", 18, "AURA", OTHER, "AURA"),
    _p(1, "0x93ed3fbe21207ec2e8f2d3c3de6e058cb73bc04d", "PNK", 18, "PNK", OTHER, "PNK"),
    _p(1, "0x40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f", "GHO", 18, "GHO", STABLE, "GHO"),
    _p(1, "0x39b8b6385416f4ca36a20319f70d28621895279d", "EURe", 18, "EURe", STABLE, "EURE"),
    _p(1, "0xb58e61c3098d85632df34eecfb899a1ed80921cb", "ZCHF", 18, "ZCHF", STABLE, "ZCHF"),
    _p(1, "0x8c213ee79581ff4984583c6a801e5263418c4b86", "JTRSY", 6, "JTRSY", RWA, "JTRSY"),
    _p(1, "0x1e2c4fb7ede391d116e6b41cd0608260e8801d59", "bCSPX", 18, "bCSPX", RWA, "BCSPX"),
    _p(1, "0x52d134c6db5889fad3542a09eaf7aa90c0fdf9e4", "bIBTA", 18, "bIBTA", RWA, "BIBTA"),
    # SparkLend supply tokens are 1:1 claims on their reserve.
    _p(1, "0x7b481acc9fdaddc9af2cbea1ff2342cb1733e50f", "spGNO", 18, "spGNO", GNOC, "GNO",
       basis="proxy", note="SparkLend supply token, 1:1 with GNO"),
    _p(1, "0x59cd1c87501baa753d0b5b5ab5d8416a45cd71db", "spWETH", 18, "spWETH", ETH, "WETH",
       basis="proxy", note="SparkLend supply token, 1:1 with WETH"),
    _p(1, "0x12b54025c112aa61face2cdb7118740875a566e9", "spwstETH", 18, "spwstETH", ETH,
       "WSTETH", basis="proxy", note="SparkLend supply token, 1:1 with wstETH"),
    # --- Ethereum: reviewed real tokens without a hub series -------------------------
    # Peg proxies (approved 2026-09-25): without them Ethereum history missed up to
    # 90% of its value (49.7k stETH in 2021-11). stETH trades ~1:1 with ETH (the
    # ~5% June 2022 depeg is not captured); DAI and USDS are ~$1 via xDAI's series.
    _p(1, "0xae7ab96520de3a18e5e111b5eaab095312d7fe84", "stETH", 18, "stETH", ETH, "WETH",
       basis="proxy", note="priced with the WETH series (1:1 staking peg)"),
    _p(1, "0x6b175474e89094c44da98b954eedeac495271d0f", "DAI", 18, "DAI", STABLE, "XDAI",
       basis="proxy", note="priced with the xDAI series (DAI-backed, ~$1)"),
    _p(1, "0xdc035d45d973e3ec169d2276ddab16f1e407384f", "USDS", 18, "USDS", STABLE, "XDAI",
       basis="proxy", note="priced with the xDAI series (1:1 DAI-convertible, ~$1)"),
    _l(1, "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd", "sUSDS", 18, "sUSDS", STABLE),
    _l(1, "0x83f20f44975d03b1b09e64809b757c47f942beea", "sDAI", 18, "sDAI (Ethereum)", STABLE),
    _l(1, "0x28b3a8fb53b741a8fd78c0fb9a6b2393d896a43d", "spUSDC", 6, "spUSDC (Ethereum)", STABLE),
    _l(1, "0x1abaea1f7c830bd89acc67ec4af516284b1bc33c", "EURC", 6, "EURC", STABLE),
    _l(1, "0x5f98805a4e8be255a32880fdec7f6728c6568ba0", "LUSD", 18, "LUSD", STABLE),
    _l(1, "0x19062190b1925b5b6689d7073fdfc8c2976ef8cb", "BZZ", 16, "BZZ", OTHER),
    _l(1, "0xd057b63f5e69cf1b929b356b579cba08d7688048", "vCOW", 18, "vCOW", OTHER,
       note="vesting token; not freely convertible, never priced as COW"),
    _l(1, "0xc944e90c64b2c07662a292be6244bdf05cda44a7", "GRT", 18, "GRT", OTHER),
    _l(1, "0x5a98fcbea516cf06857215779fd812ca3bef1b32", "LDO", 18, "LDO", OTHER),
    _l(1, "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9", "AAVE", 18, "AAVE", OTHER),
    _l(1, "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2", "MKR", 18, "MKR", OTHER),
    _l(1, "0xc00e94cb662c3520282e6f5717214004a7f26888", "COMP", 18, "COMP", OTHER),
    _l(1, "0xd533a949740bb3306d119cc777fa900ba034cd52", "CRV", 18, "CRV", OTHER),
    _l(1, "0xc18360217d8f7ab5e7c516566761ea12ce7f9d72", "ENS", 18, "ENS", OTHER),
    _l(1, "0x111111111117dc0aa78b770fa6a738034120c302", "1INCH", 18, "1INCH", OTHER),
    _l(1, "0x6b3595068778dd592e39a122f4f5a5cf09c90fe2", "SUSHI", 18, "SUSHI", OTHER),
    _l(1, "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", "UNI", 18, "UNI", OTHER),
    _l(1, "0x3432b6a60d23ca0dfca7761b7ab56459d9c964d0", "FXS", 18, "FXS", OTHER),
    _l(1, "0xfeef77d3f69374f66429c91d732a244f074bdf74", "cvxFXS", 18, "cvxFXS", OTHER),
    _l(1, "0x6dea81c8171d0ba574754ef6f8b412f2ed88c54d", "LQTY", 18, "LQTY", OTHER),
    _l(1, "0xd33526068d116ce69f19a9ee46f0bd304f21a51f", "RPL", 18, "RPL", OTHER),
    _l(1, "0x9d65ff81a3c488d585bbfb0bfe3c7707c7917f54", "SSV", 18, "SSV", OTHER),
    _l(1, "0xae78736cd615f374d3085123a210448e74fc6393", "rETH", 18, "rETH", ETH),
    _l(1, "0xf1c9acdc66974dfb6decb12aa385b9cd01190e38", "osETH", 18, "osETH", ETH),
    _l(1, "0xfe2e637202056d30016725477c5da089ab0a043a", "sETH2", 18, "sETH2", ETH),
    _l(1, "0x20bc832ca081b91433ff6c17f85701b6e92486c5", "rETH2", 18, "rETH2", ETH),
    _l(1, "0x616e8bfa43f920657b3497dbf40d6b1a02d4608d", "auraBAL", 18, "auraBAL", OTHER),
    _l(1, "0x928966752dc0cc0d7babe343fc2937ba13a5120c", "rAURA", 18, "rAURA", OTHER),
    _l(1, "0x58b9cb810a68a7f3e1e4f8cb45d1b9b3c79705e8", "CLEAR", 18, "CLEAR", OTHER),
    _l(1, "0xc5102fe9359fd9a28f877a67e36b0f050d81a3cc", "HOP", 18, "HOP", OTHER),
    _l(1, "0x255aa6df07540cb5d3d297f0d0d4d84cb52bc8e6", "RDN", 18, "RDN", OTHER),
    _l(1, "0x1a5f9352af8af974bfc03399e3767df6370d82e4", "OWL", 18, "OWL", OTHER),
    _l(1, "0x01e39271d33342f9167f948a5c9e67f004bf802d", "SILO", 18, "SILO", OTHER),
    _l(1, "0x09d6f0f5a21f5be4f59e209747e2d07f50bc694c", "NFTFI", 18, "NFTFI", OTHER),
    _l(1, "0xcafe001067cdef266afb7eb5a286dcfd277f3de5", "PSP", 18, "PSP", OTHER),
    _l(1, "0x44709a920fccf795fbc57baa433cc3dd53c44dbe", "RADAR", 18, "RADAR", OTHER),
    _l(1, "0x2d94aa3e47d9d5024503ca8491fce9a2fb4da198", "BANK", 18, "BANK", OTHER),
    _l(1, "0x0d8775f648430679a709e98d2b0cb6250d2887ef", "BAT", 18, "BAT", OTHER),
    _l(1, "0x7d1afa7b718fb893db30a3abc0cfc608aacfebb0", "MATIC", 18, "MATIC", OTHER),
    _l(1, "0xd26114cd6ee289accf82350c8d8487fedb8a0c07", "OMG", 18, "OMG", OTHER),
    _l(1, "0x8290333cef9e6d528dd5618fb97a76f268f3edd4", "ANKR", 18, "ANKR", OTHER),
    _l(1, "0xef3a930e1ffffacd2fc13434ac81bd278b0ecc8d", "FIS", 18, "FIS", OTHER),
    _l(1, "0xde30da39c46104798bb5aa3fe8b9e0e1f348163f", "GTC", 18, "GTC", OTHER),
    _l(1, "0xda007777d86ac6d989cc9f79a73261b3fc5e0da0", "NODE", 18, "NODE", OTHER),
    # --- Gnosis Chain (chain 100): hub-priced ----------------------------------------
    _p(100, "0x9c58bacc331c9aa871afd802db6379a98e80cedb", "GNO", 18, "GNO", GNOC, "GNO"),
    _p(100, "0xe91d153e0b41518a2ce8dd3d7944fa863463a97d", "WXDAI", 18, "WXDAI", STABLE, "WXDAI"),
    _p(100, "0xddafbb505ad214d7b80b1f830fccc89b60fb7a83", "USDC", 6, "USDC", STABLE, "USDC",
       note="Omnibridge USDC"),
    _p(100, "0x2a22f9c3b484c3629090feed35f17ff8f88f76f0", "USDC.e", 6, "USDC", STABLE, "USDC.E",
       note="Bridged USDC (Gnosis), Circle standard"),
    _p(100, "0x4ecaba5870353805a9f068101a40e0f32ed605c6", "USDT", 6, "USDT", STABLE, "USDT"),
    _p(100, "0x6a023ccd1ff6f2045c3309768ead9e68f978f6e1", "WETH", 18, "WETH", ETH, "WETH"),
    _p(100, "0x6c76971f98945ae98dd7d4dfca8711ebea946ea6", "wstETH", 18, "wstETH", ETH, "WSTETH"),
    _p(100, "0x8e5bbbb09ed1ebde8674cda39a0c169401db4252", "WBTC", 8, "WBTC", BTC, "WBTC"),
    _p(100, "0xaf204776c7245bf4147c2612bf6e5972ee483701", "sDAI", 18, "sDAI", STABLE, "SDAI",
       note="Savings xDAI"),
    _p(100, "0x177127622c4a00f3d409b75571e12cb3c8973d3c", "COW", 18, "COW", OTHER, "COW"),
    _p(100, "0x4d18815d14fe5c3304e87b3fa18318baa5c23820", "SAFE", 18, "SAFE", OTHER, "SAFE"),
    _p(100, "0xd057604a14982fe8d88c5fc25aac3267ea142a08", "HOPR", 18, "HOPR", OTHER, "HOPR"),
    _p(100, "0x37b60f4e9a31a64ccc0024dce7d0fd07eaa0f7b3", "PNK", 18, "PNK", OTHER, "PNK"),
    _p(100, "0x1509706a6c66ca549ff0cb464de88231ddbe213b", "AURA", 18, "AURA", OTHER, "AURA"),
    _p(100, "0x7ef541e2a22058048904fe5744f9c7e4c57af717", "BAL", 18, "BAL", OTHER, "BAL"),
    _p(100, "0xfc421ad3c883bf9e7c4f42de845c4e4405799e73", "GHO", 18, "GHO", STABLE, "GHO"),
    _p(100, "0xd4dd9e2f021bb459d5a5f6c24c12fe09c5d45553", "ZCHF", 18, "ZCHF", STABLE, "ZCHF"),
    _p(100, "0x6165946250dd04740ab1409217e95a4f38374fe9", "svZCHF", 18, "svZCHF", STABLE, "SVZCHF"),
    _p(100, "0xfecb3f7c54e2caae9dc6ac9060a822d47e053760", "BRLA", 18, "BRLA", STABLE, "BRLA"),
    _p(100, "0x0a06c8354a6cc1a07549a38701eac205942e3ac6", "BRZ", 18, "BRZ", STABLE, "BRZ"),
    _p(100, "0xf490c80aae5f2616d3e3bda2483e30c4cb21d1a0", "osGNO", 18, "osGNO", GNOC, "OSGNO"),
    _p(100, "0xa4ef9da5ba71cc0d2e5e877a910a37ec43420445", "sGNO", 18, "sGNO", GNOC, "SGNO"),
    _p(100, "0x0ac34fe133bde3a2ef589a18a4e10b6a7d253829", "OC-sDAI", 18, "OC-sDAI", STABLE, "OC-SDAI"),
    # Monerium: v1 and v2 share one ledger after the migration (both report the same
    # balance), so v1 is valued only BEFORE it and is a retired mirror after.
    _p(100, "0xcb444e90d8198415266c6a2724b7900fb12fc56e", "EURe", 18, "EURe", STABLE, "EURE",
       valid_to=MONERIUM_MIGRATION, note="Monerium EURe v1"),
    _r(100, "0xcb444e90d8198415266c6a2724b7900fb12fc56e", "EURe v1", 18, "EURe", STABLE,
       valid_from=MONERIUM_MIGRATION, note="Monerium EURe v1 mirrors the v2 ledger since the 2024-08-25 migration"),
    _p(100, "0x420ca0f9b9b604ce0fd9c18ef134c705e5fa3430", "EURe", 18, "EURe", STABLE, "EURE",
       note="Monerium EURe v2"),
    _p(100, "0x5cb9073902f2035222b9749f8fb0c9bfe5527108", "GBPe", 18, "GBPe", STABLE, "GBPE",
       valid_to=MONERIUM_MIGRATION, note="Monerium GBPe v1"),
    _r(100, "0x5cb9073902f2035222b9749f8fb0c9bfe5527108", "GBPe v1", 18, "GBPe", STABLE,
       valid_from=MONERIUM_MIGRATION, note="Monerium GBPe v1 mirrors the v2 ledger since the 2024-08-25 migration"),
    _p(100, "0x8e34bfec4f6eb781f9743d9b4af99cd23f9b7053", "GBPe", 18, "GBPe", STABLE, "GBPE",
       note="Monerium GBPe v2"),
    # Aave v3 / SparkLend supply tokens (hub prices them 1:1 with their reserve).
    _p(100, "0xa1fa064a85266e2ca82dee5c5ccec84df445760e", "aGnoGNO", 18, "aGnoGNO", GNOC, "AGNOGNO"),
    _p(100, "0xd0dd6cef72143e22cced4867eb0d5f2328715533", "aGnoWXDAI", 18, "aGnoWXDAI", STABLE, "AGNOWXDAI"),
    _p(100, "0xc6b7aca6de8a6044e0e32d0c841a89244a10d284", "aGnoUSDC", 6, "aGnoUSDC", STABLE, "AGNOUSDC"),
    _p(100, "0xc0333cb85b59a788d8c7cae5e1fd6e229a3e5a65", "aGnoUSDCe", 6, "aGnoUSDCe", STABLE, "AGNOUSDCE"),
    _p(100, "0xedbc7449a9b594ca4e053d9737ec5dc4cbccbfb2", "aGnoEURe", 18, "aGnoEURe", STABLE, "AGNOEURE"),
    _p(100, "0x7a5c3860a77a8dc1b225bd46d0fb2ac1c6d191bc", "aGnosDAI", 18, "aGnosDAI", STABLE, "AGNOSDAI"),
    _p(100, "0x5671b0b8ac13dc7813d36b99c21c53f6cd376a14", "spGNO", 18, "spGNO", GNOC, "SPGNO"),
    _p(100, "0xc9fe2d32e96bb364c7d29f3663ed3b27e30767bb", "spWXDAI", 18, "spWXDAI", STABLE, "SPWXDAI"),
    _p(100, "0x6dc304337bf3eb397241d1889cae7da638e6e782", "spEURe", 18, "spEURe", STABLE, "SPEURE"),
    _p(100, "0x5850d127a04ed0b4f1fcdfb051b3409fb9fe6b90", "spUSDC", 6, "spUSDC", STABLE, "SPUSDC"),
    _p(100, "0xa34db0ee8f84c4b90ed268df5abbe7dcd3c277ec", "spUSDC.e", 6, "spUSDC.e", STABLE, "SPUSDC.E"),
    _p(100, "0x08b0caebe352c3613302774cd9b82d08afd7bdc4", "spUSDT", 6, "spUSDT", STABLE, "SPUSDT"),
    _p(100, "0x629d562e92fed431122e865cc650bc6bde6b96b0", "spWETH", 18, "spWETH", ETH, "SPWETH"),
    _p(100, "0x9ee4271e17e3a427678344fd2ee64663cb78b4be", "spwstETH", 18, "spwstETH", ETH, "SPWSTETH"),
    _p(100, "0xe877b96caf9f180916bf2b5ce7ea8069e0123182", "spsDAI", 18, "spsDAI", STABLE, "SPSDAI"),
    # Aave v3 supply tokens the hub has no series for: 1:1 claims on the reserve.
    _p(100, "0x3fdcec11b4f15c79d483aedc56f37d302837cf4d", "aGnoGHO", 18, "aGnoGHO", STABLE,
       "GHO", basis="proxy", note="Aave v3 supply token, 1:1 with GHO"),
    _p(100, "0xa818f1b57c201e092c4a2017a91815034326efd1", "aGnoWETH", 18, "aGnoWETH", ETH,
       "WETH", basis="proxy", note="Aave v3 supply token, 1:1 with WETH"),
    _p(100, "0x23e4e76d01b2002be436ce8d6044b0aa2f68b68a", "aGnowstETH", 18, "aGnowstETH", ETH,
       "WSTETH", basis="proxy", note="Aave v3 supply token, 1:1 with wstETH"),
    _p(100, "0xd4fdec44db9d44b8f2b6d529620f9c0c7066a2c1", "wxHOPR", 18, "wxHOPR", OTHER,
       "HOPR", basis="proxy", note="Wrapped xHOPR, 1:1 with HOPR"),
    # Backed Finance RWA tokens (same deterministic addresses as on Ethereum).
    _p(100, "0x2f123cf3f37ce3328cc9b5b8415f9ec5109b45e7", "bC3M", 18, "bC3M", RWA, "BC3M"),
    _p(100, "0xbbcb0356bb9e6b3faa5cbf9e5f36185d53403ac9", "bCOIN", 18, "bCOIN", RWA, "BCOIN"),
    _p(100, "0x1e2c4fb7ede391d116e6b41cd0608260e8801d59", "bCSPX", 18, "bCSPX", RWA, "BCSPX"),
    _p(100, "0x20c64dee8fda5269a78f2d5bdba861ca1d83df7a", "bHIGH", 18, "bHIGH", RWA, "BHIGH"),
    _p(100, "0xca30c93b02514f86d5c86a6e375e3a330b435fb5", "bIB01", 18, "bIB01", RWA, "BIB01"),
    _p(100, "0x52d134c6db5889fad3542a09eaf7aa90c0fdf9e4", "bIBTA", 18, "bIBTA", RWA, "BIBTA"),
    _p(100, "0xac28c9178acc8ba4a11a29e013a3a2627086e422", "bMSTR", 18, "bMSTR", RWA, "BMSTR"),
    _p(100, "0xa34c5e0abe843e10461e2c9586ea03e55dbcc495", "bNVDA", 18, "bNVDA", RWA, "BNVDA"),
    _p(100, "0x14a5f2872396802c3cc8942a39ab3e4118ee5038", "bTSLA", 18, "bTSLA", RWA, "BTSLA"),
    _p(100, "0x8ad3c73f833d3f9a523ab01476625f269aeb7cf0", "TSLAx", 18, "TSLAx", RWA, "TSLAX"),
    # --- Gnosis Chain: reviewed real tokens without a hub series ----------------------
    _p(100, "0xfc8b2690f66b46fec8b3ceeb95ff4ac35a0054bc", "DAI", 18, "DAI", STABLE, "XDAI",
       basis="proxy", note="Dai Token on xDai (Omnibridge), priced with the xDAI series"),
    _p(100, "0x3c037849a8ffcf19886e2f5b04f293b7847d0377", "stETH", 18, "stETH", ETH, "WETH",
       basis="proxy", note="Omnibridge stETH, priced with the WETH series"),
    _l(100, "0xd10cc63531a514bba7789682e487add1f15a51e2", "USDC (xDai bridge)", 18, "USDC", STABLE,
       note="legacy 18-decimal bridged USDC"),
    _l(100, "0xc90132d5d1b87730da162ec9cd34885828769cc3", "USDT (xDai bridge)", 18, "USDT", STABLE,
       note="legacy 18-decimal bridged USDT"),
    _l(100, "0xcd2f64112ec04e21cc56f7bf2294a6a37790305e", "USDC.e (Lucid)", 6, "USDC", STABLE,
       note="Bridged USDC (Lucid)"),
    _l(100, "0xa555d5344f6fb6c65da19e403cb4c1ec4a1a5ee3", "BREAD", 18, "BREAD", STABLE,
       note="Breadchain stablecoin"),
    _l(100, "0xca03a578af8a0dc66a3e6096d0ea560d68d85ab1", "BAKE", 18, "BAKE", OTHER,
       note="Breadchain BreadBake"),
    _l(100, "0x712b3d230f3c1c19db860d80619288b1f0bdd0bd", "CRV", 18, "CRV", OTHER),
    _l(100, "0x4537e328bf7e4efa29d05caea260d7fe26af9d74", "UNI", 18, "UNI", OTHER),
    _l(100, "0x5fd896d248fbfa54d26855c267859eb1b4daee72", "MKR", 18, "MKR", OTHER),
    _l(100, "0xdf6ff92bfdc1e8be45177dc1f4845d391d3ad8fd", "COMP", 18, "COMP", OTHER),
    _l(100, "0x2995d1317dcd4f0ab89f4ae60f3f020a4f17c7ce", "SUSHI", 18, "SUSHI", OTHER),
    _l(100, "0xfadc59d012ba3c110b08a15b7755a5cb7cbe77d7", "GRT", 18, "GRT", OTHER),
    _l(100, "0x2853f6e9605419ccf38d102fb1fb3961904ae263", "GTC", 18, "GTC", OTHER),
    _l(100, "0xb0c5f3100a4d9d9532a4cfd68c55f1ae8da987eb", "HAUS", 18, "HAUS", OTHER),
    _l(100, "0x10010078a54396f62c96df8532dc2b4847d47ed3", "HND", 18, "HND", OTHER),
    _l(100, "0xc5102fe9359fd9a28f877a67e36b0f050d81a3cc", "HOP", 18, "HOP", OTHER),
    _l(100, "0xc60e38c6352875c051b481cbe79dd0383adb7817", "NODE", 18, "NODE", OTHER),
    _l(100, "0xdfa46478f9e5ea86d57387849598dbfb2e964b02", "QI", 18, "QI", OTHER),
    _l(100, "0x18e9262e68cc6c6004db93105cc7c001bb103e49", "RAID", 18, "RAID", OTHER),
    _l(100, "0x532801ed6f82fffd2dab70a19fc2d7b2772c4f4b", "SWPR", 18, "SWPR", OTHER),
    _l(100, "0xc45b3c1c24d5f54e7a2cf288ac668c74dd507a84", "SYMM", 18, "SYMM", OTHER),
    _l(100, "0xeddd81e0792e764501aae206eb432399a0268db5", "TRAC", 18, "TRAC", OTHER),
    _l(100, "0x988d1be68f2c5cde2516a2287c59bd6302b7d20d", "PUNK", 18, "PUNK", OTHER),
    _l(100, "0x4f4f9b8d5b4d0dc10506e5551b0513b61fd59e75", "GIV", 18, "GIV", OTHER),
    _l(100, "0xc791240d1f2def5938e2031364ff4ed887133c3d", "rETH", 18, "rETH", ETH),
    _l(100, "0xc20c9c13e853fc64d054b73ff21d3636b2d97eab", "vCOW", 18, "vCOW", OTHER,
       note="vesting token; not freely convertible, never priced as COW"),
    _l(100, "0x63803b132a59e481920c4c46a981bf45555b0421", "auraBAL", 18, "auraBAL", OTHER),
    _l(100, "0xdbf3ea6f5bee45c02255b2c26a16f300502f68da", "xBZZ", 16, "BZZ", OTHER),
    # Treasury-owned Balancer pool shares: real value, NOT valued here (a pool
    # share needs the pool's reserves; follow-up via v_pool_token_balances_published).
    _l(100, "0xb5814811dc4fc2ac127a1f8fb708460bf9fad619", "osGNOGNO", 18, "osGNOGNO", GNOC,
       note="Balancer pool share GNO/osGNO; not valued"),
    _l(100, "0x4a053d86bcccdfb6f85c46b38c5873129212dc1f", "USDCesDAI", 18, "USDCesDAI", STABLE,
       note="Balancer pool share USDC.e/sDAI; not valued"),
    _l(100, "0x0ecec6f5276d2ec6bb864f063d2b76393d6a1a74", "USDCeEURe", 18, "USDCeEURe", STABLE,
       note="Balancer pool share USDC.e/EURe; not valued"),
    _l(100, "0x9c9ddda2a6bda693856b9081baebbe1444ffcc37", "USDCeWXDAI", 18, "USDCeWXDAI", STABLE,
       note="Balancer pool share USDC.e/WXDAI; not valued"),
    _l(100, "0xfb8b95fb2296a0ad4b6b1419fdaa5aa5f13e4009", "USDCeBRLA", 18, "USDCeBRLA", STABLE,
       note="Balancer pool share BRLA/USDC.e; not valued"),
    _l(100, "0x9af34331175e053bcff330d7bb7a6ea2ba53e83d", "EUReZCHF", 18, "EUReZCHF", STABLE,
       note="Balancer pool share ZCHF/EURe; not valued"),
    _l(100, "0x85d62ba914912803588fcd5a722483235b785263", "WETHwstETH", 18, "WETHwstETH", ETH,
       note="Balancer pool share WETH/wstETH; not valued"),
)

# ---------------------------------------------------------------------------
# Wallet labels
# ---------------------------------------------------------------------------

#: Community labels for the 23 census wallets. Source: the README of
#: github.com/koeppelmann/GnosisDAO_treasury — the same list the rpc-state-indexer
#: vendors as config/<chain>/vendored/treasury_addresses.csv (checked address by
#: address on 2026-09-25). The README disclaims correctness, so the UI shows the
#: address beside every label and attributes the source in a tooltip.
WALLET_LABEL_SOURCE = "koeppelmann/GnosisDAO_treasury README (community list, unverified)"
WALLET_LABELS: dict[str, str] = {
    "0x458cd345b4c05e8df39d0a07220feb4ec19f5e6f": "GNO Main Treasury",
    "0x849d52316331967b6ff1198e5e32a0eb168d039d": "Token Holdings (GNO, SAFE, COW)",
    "0x509ad7278a2f6530bc24590c83e93faf8fd46e99": "Stables & Staking",
    "0xa5c629e04e563355c30885b62928fd6e03558548": "ETH Staking",
    "0x15a954001bb47890a4c46a7fe9f06f7c39ff3d68": "wstETH Primary",
    "0x4971dd016127f390a3ef6b956ff944d0e2e1e462": "COW & Mixed",
    "0x9065a0f9545817d18b58436771b4d87bda8f008b": "Aave Lending",
    "0x10e4597ff93cbee194f4879f8f1d54a370db6969": "Gnosis Chain Treasury",
    "0x2923c1b5313f7375fdaee80b7745106debc1b53e": "LTF Holdings",
    "0x0da0c3e52c977ed3cbc641ff02dd271c3ed55afe": "COW Vesting",
    "0x5be8ab1c28ee22cdf9b136feda7d8f20876bfc0f": "Gnosis Chain Stables",
    "0x689d4bd36bc1938af5ca2673c3c753235e3b4d2b": "GNO Reserve",
    "0x399948eee21c5627adb7de4a7efe712245d48442": "Swarm (BZZ)",
    "0x45a09fabd540b54f499212b3f579f0cf3393ab6b": "Swarm (BZZ) 2",
    "0x7eea4286e9e82ba332f49400d037609bb1cf00da": "GNO Small",
    "0x93ebf01356f44a1b0734081b0440bff7bacf72ec": "Swarm (BZZ) 3",
    "0x6bbe78ee9e474842dbd4ab4987b3cefe88426a92": "xDAI Operations",
    "0x0668792caf78d5bad6cd0b8ece032dfa7c11ac60": "Swarm (BZZ) 4",
    "0xcdf50be9061086e2ecfe6e4a1bf9164d43568eec": "GNO Micro",
    "0x823a92ab789b15b88f43d798c332d6f38a32f0f6": "Swarm (BZZ) 5",
    "0x813804d2d0820a6d8139946229bb840591baeb47": "Empty Wallet",
    "0x2bd0563e3e2c55eddabe4e469a02ca6652fb9e4a": "Empty Wallet 2",
    "0x604e4557e9020841f4e8eb98148de3d3cdea350c": "Gnosis Ltd.",
}

# ---------------------------------------------------------------------------
# Spam classifier (SQL is authoritative; the Python twin below is for tests)
# ---------------------------------------------------------------------------

#: Lure text in symbol or name: URLs, domain-looking words (including the
#: "yield-usd .net" space-before-dot evasion), call-to-action verbs, and dollar
#: amounts. Bare "reward"/"redeem"/"$" are NOT lures — "StakeWise Reward ETH2",
#: "Redeemed AURA" and "Backed IBTA $ Treasury Bond" are real tokens — and short
#: ambiguous TLDs (.fi, .co, .me, ...) are left out ("Curve.fi DAI/USDC/USDT"
#: is a real LP token). ``.finance`` IS a lure: every holding named *.finance
#: here is a 2020 yield-farm scam. One string,
#: valid in both RE2 and Python ``re`` (use re.ASCII in Python to match RE2's
#: ASCII ``\b``).
LURE_RE = (
    r"(?i)(https?:|www\.|t\.me/"
    r"|\b(visit|claim|claims|airdrop|giveaway|giveaways|voucher|coupon)\b"
    r"|\$\s*[0-9]|[0-9]\s*\$"
    r"|[a-z0-9-]\s?\.\s?(com|org|net|io|xyz|cfd|gift|site|online|network|events|app"
    r"|top|vip|live|win|us|games|farm|finance|pro|club|fund|tech|world|link|cash"
    r"|money|click|space|website|info|biz|gg)\b)"
)
#: Characters that only appear in a token symbol/name to deceive: combining and
#: enclosing marks, format controls (zero-width, bidi, invisible operators),
#: private use, controls, spacing modifier letters (U+02F3 is the fake dot in
#: "SHIBSWAP˳ORG"), Hangul/Braille fillers, homoglyph scripts, mathematical
#: alphanumerics and fullwidth forms. Variation selectors are stripped first
#: (VARIATION_SELECTOR_RE) so an emoji like "⚔️" in "Raid Guild Token ⚔️" passes.
OBFUSCATION_RE = (
    r"[\p{Mn}\p{Me}\p{Cf}\p{Co}\p{Cc}\x{02B0}-\x{02FF}\x{A700}-\x{A71F}"
    r"\x{2800}\x{3164}\x{115F}\x{1160}\x{FFA0}\x{FFFC}\x{FFFD}"
    r"\x{1D400}-\x{1D7FF}\x{FF01}-\x{FF5E}]"
    r"|[\p{Lisu}\p{Cyrillic}\p{Greek}\p{Cherokee}\p{Armenian}]"
)
VARIATION_SELECTOR_RE = r"[\x{FE00}-\x{FE0F}\x{E0100}-\x{E01EF}]"

MAX_SYMBOL_LEN = 32
MAX_NAME_LEN = 96
MASS_AIRDROP_SHARE = 0.75
MASS_AIRDROP_MIN_WALLETS = 10

SPAM_REASONS = ("impersonation", "lure", "obfuscated", "malformed", "mass_airdrop")
TOKEN_CLASSES = ("priced", "listed", "unverified", "spam", "retired_mirror")

_FOLD_RE = re.compile(r"[^A-Za-z0-9.]")
_LURE_PY = re.compile(LURE_RE, re.ASCII)


def fold_symbol(symbol: str | None) -> str:
    """ASCII fold used for impersonation: drop everything but [A-Za-z0-9.], upper.

    Mirrors ``upper(replaceRegexpAll(symbol, '[^A-Za-z0-9.]', ''))`` in SQL, so
    "US͏DC" folds to "USDC" and is caught as an impersonation of USDC.
    """
    return _FOLD_RE.sub("", symbol or "").upper()


_HOMOGLYPH_SCRIPTS = ("LISU ", "CYRILLIC ", "GREEK ", "CHEROKEE ", "ARMENIAN ")


def _obfuscated_py(text: str) -> bool:
    for char in text:
        code = ord(char)
        if 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF:
            continue  # variation selectors are stripped before the SQL match
        if unicodedata.category(char) in ("Mn", "Me", "Cf", "Co", "Cc"):
            return True
        if (0x02B0 <= code <= 0x02FF or 0xA700 <= code <= 0xA71F
                or code in (0x2800, 0x3164, 0x115F, 0x1160, 0xFFA0, 0xFFFC, 0xFFFD)
                or 0x1D400 <= code <= 0x1D7FF or 0xFF01 <= code <= 0xFF5E):
            return True
        name = unicodedata.name(char, "")
        if name.startswith(_HOMOGLYPH_SCRIPTS):
            return True
    return False


def classify(
    chain_id: int,
    address: str,
    symbol: str | None,
    name: str | None,
    wallets: int,
    active_wallets: int,
    on_date: str = PRESENT,
) -> str:
    """Python twin of ``_expr_treasury_spam_reason.sql``: the spam reason, or ''.

    Evaluated against the shared fixture table (tests/treasury_spam_fixtures.py)
    here AND inside ClickHouse by the live smoke, which is authoritative.
    """
    if entry_at(chain_id, address, on_date) is not None:
        return ""
    sym = symbol or ""
    nam = name or ""
    text = f"{sym} {nam}"
    if fold_symbol(sym) in verified_folds():
        return "impersonation"
    if _LURE_PY.search(text):
        return "lure"
    if _obfuscated_py(text):
        return "obfuscated"
    if len(sym) > MAX_SYMBOL_LEN or len(nam) > MAX_NAME_LEN:
        return "malformed"
    if (active_wallets >= MASS_AIRDROP_MIN_WALLETS
            and wallets >= math.ceil(MASS_AIRDROP_SHARE * active_wallets)):
        return "mass_airdrop"
    return ""


# ---------------------------------------------------------------------------
# Lookups and binds
# ---------------------------------------------------------------------------


def entry_at(chain_id: int, address: str, on_date: str) -> TokenEntry | None:
    """The registry entry for (chain, address) valid on ``on_date`` (ISO)."""
    addr = address.lower()
    for entry in TOKENS:
        if (entry.chain_id == chain_id and entry.address == addr
                and entry.valid_from <= on_date < entry.valid_to):
            return entry
    return None


@lru_cache(maxsize=1)
def verified_folds() -> frozenset[str]:
    """Folded symbols of every registry token on ANY chain. Global on purpose: a
    fake "WXDAI" on Ethereum is as much an impersonation as one on Gnosis."""
    return frozenset(fold_symbol(entry.symbol.split(" ")[0]) for entry in TOKENS)


def wallet_label(address: str) -> str:
    return WALLET_LABELS.get(address.lower(), "")


def siblings(chain_id: int, address: str) -> list[str]:
    """``chain:address`` of the same asset elsewhere (other chains or bridged
    representations), for the token page's chain switcher."""
    addr = address.lower()
    keys = {e.asset_key for e in TOKENS if e.chain_id == chain_id and e.address == addr}
    if not keys:
        return []
    # Current entries only: a retired mirror's historical (pre-migration) entry
    # must not offer a jump to a contract that now just mirrors its successor.
    return sorted({
        f"{e.chain_id}:{e.address}" for e in TOKENS
        if e.asset_key in keys and not (e.chain_id == chain_id and e.address == addr)
        and e.valid_from <= PRESENT < e.valid_to and e.role != "retired_mirror"
    })


@lru_cache(maxsize=1)
def bind_params() -> dict[str, Any]:
    """Parallel arrays bound into the treasury SQL. JSON-serialisable (cache keys)."""
    tokens = sorted(TOKENS, key=lambda e: (e.chain_id, e.address, e.valid_from))
    folds = sorted(verified_folds())
    labels = sorted(WALLET_LABELS.items())
    return {
        "reg_chain": [e.chain_id for e in tokens],
        "reg_token": [e.address for e in tokens],
        "reg_role": [e.role for e in tokens],
        "reg_psym": [e.price_symbol for e in tokens],
        "reg_basis": [e.price_basis for e in tokens],
        "reg_symbol": [e.symbol for e in tokens],
        "reg_dec": [e.decimals for e in tokens],
        "reg_asset": [e.asset_key for e in tokens],
        "reg_class": [e.asset_class for e in tokens],
        "reg_from": [e.valid_from for e in tokens],
        "reg_to": [e.valid_to for e in tokens],
        "hub_syms": sorted({e.price_symbol for e in tokens if e.price_symbol}),
        "vs_fold": folds,
        "label_addr": [address for address, _ in labels],
        "label_name": [name for _, name in labels],
        "label_source": WALLET_LABEL_SOURCE,
        "lure_re": LURE_RE,
        "obf_re": OBFUSCATION_RE,
        "vs_re": VARIATION_SELECTOR_RE,
    }


def _match_rank(query: str, text: str) -> int | None:
    """0 exact, 1 prefix, 2 substring (case-insensitive), None when no match — the
    same ladder the SQL text search uses, so registry hits merge fairly."""
    value = text.lower()
    if value == query:
        return 0
    if value.startswith(query):
        return 1
    if query in value:
        return 2
    return None


def search(query: str) -> list[dict[str, Any]]:
    """Treasury entity candidates for a free-text query: a wallet address, a wallet
    label, a registry symbol, or a registered token address. Pure data — no
    ClickHouse round trip. Each carries ``match_rank`` (0 exact, 1 prefix, 2
    substring) so the caller can merge it with the SQL candidates."""
    q = query.strip().lower()
    if not q:
        return []
    out: list[dict[str, Any]] = []
    chains = (1, 100)
    if re.fullmatch(r"0x[0-9a-f]{40}", q):
        if q in WALLET_LABELS:
            for chain in chains:
                out.append({"entity_type": "treasury_wallet", "identifier": f"{chain}:{q}",
                            "label": WALLET_LABELS[q], "role": "treasury wallet",
                            "evidence_count": 0, "match_rank": 0})
        seen_tokens: set[str] = set()
        for entry in TOKENS:
            ident = f"{entry.chain_id}:{entry.address}"
            if entry.address == q and entry.role != "retired_mirror" and ident not in seen_tokens:
                seen_tokens.add(ident)
                out.append({"entity_type": "treasury_token", "identifier": ident,
                            "label": entry.symbol, "role": "treasury token",
                            "evidence_count": 0, "match_rank": 0})
        return out
    for address, label in sorted(WALLET_LABELS.items()):
        rank = _match_rank(q, label)
        if rank is not None:
            for chain in chains:
                out.append({"entity_type": "treasury_wallet",
                            "identifier": f"{chain}:{address}", "label": label,
                            "role": "treasury wallet", "evidence_count": 0,
                            "match_rank": rank})
    seen: set[str] = set()
    for entry in TOKENS:
        ident = f"{entry.chain_id}:{entry.address}"
        if entry.role == "retired_mirror" or ident in seen:
            continue
        if q in (entry.symbol.lower(), entry.asset_key.lower()):
            seen.add(ident)
            out.append({"entity_type": "treasury_token", "identifier": ident,
                        "label": entry.symbol, "role": "treasury token",
                        "evidence_count": 0, "match_rank": 0})
    return out


def validate() -> list[str]:
    """Registry invariants; an empty list means valid. Hermetic (no ClickHouse)."""
    problems: list[str] = []
    addr_re = re.compile(r"^0x[0-9a-f]{40}$")
    windows: dict[tuple[int, str], list[tuple[str, str, str]]] = {}
    for entry in TOKENS:
        key = (entry.chain_id, entry.address)
        if not addr_re.match(entry.address):
            problems.append(f"{key}: address must be lowercase 0x + 40 hex")
        if entry.chain_id not in (1, 100):
            problems.append(f"{key}: unknown chain")
        if not entry.valid_from < entry.valid_to:
            problems.append(f"{key}: empty window")
        if entry.role == "priced" and not entry.price_symbol:
            problems.append(f"{key}: priced without price_symbol")
        if entry.role != "priced" and entry.price_symbol:
            problems.append(f"{key}: {entry.role} must not carry a price_symbol")
        if entry.role == "priced" and entry.price_basis not in ("direct", "proxy"):
            problems.append(f"{key}: priced needs price_basis direct|proxy")
        if entry.price_symbol and entry.price_symbol != entry.price_symbol.upper():
            problems.append(f"{key}: price_symbol must be upper-case")
        if not 0 <= entry.decimals <= 36:
            problems.append(f"{key}: implausible decimals")
        for text in (entry.symbol, entry.asset_key, entry.note):
            if "'" in text or "@" in text or "\\" in text:
                problems.append(f"{key}: text must not contain quote/@/backslash")
        windows.setdefault(key, []).append((entry.valid_from, entry.valid_to, entry.role))
    for key, spans in windows.items():
        spans.sort()
        for (_, end, _), (start, _, _) in zip(spans, spans[1:]):
            if start < end:
                problems.append(f"{key}: overlapping validity windows")
    for entry in TOKENS:
        if entry.role != "retired_mirror":
            continue
        # A mirror must point at a live priced contract for the same asset, or the
        # balance it mirrors would silently vanish from the totals.
        successor = [
            e for e in TOKENS
            if e.chain_id == entry.chain_id and e.address != entry.address
            and e.role == "priced" and e.asset_key == entry.asset_key
            and e.valid_from <= entry.valid_from < e.valid_to
        ]
        if not successor:
            problems.append(
                f"{(entry.chain_id, entry.address)}: retired_mirror without a priced successor"
            )
    for address, label in WALLET_LABELS.items():
        if not addr_re.match(address):
            problems.append(f"label {address}: address must be lowercase 0x + 40 hex")
        if not label or "'" in label or "@" in label or "\\" in label:
            problems.append(f"label {address}: empty or unsafe text")
    if len(WALLET_LABELS) != 23:
        problems.append(f"expected 23 wallet labels, found {len(WALLET_LABELS)}")
    return problems


__all__ = [
    "TOKENS", "TokenEntry", "WALLET_LABELS", "WALLET_LABEL_SOURCE", "LURE_RE",
    "OBFUSCATION_RE", "VARIATION_SELECTOR_RE", "MAX_SYMBOL_LEN", "MAX_NAME_LEN",
    "MASS_AIRDROP_SHARE", "MASS_AIRDROP_MIN_WALLETS", "SPAM_REASONS", "TOKEN_CLASSES",
    "bind_params", "classify", "entry_at", "fold_symbol", "search", "siblings",
    "validate", "verified_folds", "wallet_label",
]
