"""Shared treasury spam-classifier fixture table.

The classifier runs in ClickHouse (``_expr_treasury_spam_reason.sql``, RE2) and has
a Python twin (``treasury_registry.classify``) for hermetic tests. They cannot share
one implementation, so THIS table is what pins them together. It is evaluated in
each engine:

- Python: tests/test_treasury_registry.py::test_python_classifier_matches_the_fixture_table
- ClickHouse (authoritative for the SQL dialect):
  tests/test_governance_live_smoke.py::test_treasury_spam_fixtures_match_in_clickhouse

Positives are the real spam observed in the GnosisDAO treasury on 2026-09-25
(addresses kept where the row is a real contract). Negatives are real token names
that an over-eager rule would hide; unregistered placeholder addresses are used
where the point is to exercise the RULE rather than the registry exemption.

Row: (chain_id, address, symbol, name, wallets_holding, active_wallets, expected).
"""

from __future__ import annotations

_A = "0x" + "a1" * 20  # unregistered placeholders (never registry addresses)
_B = "0x" + "b2" * 20
_C = "0x" + "c3" * 20
_D = "0x" + "d4" * 20
_E = "0x" + "e5" * 20
_F = "0x" + "f6" * 20

TREASURY_SPAM_FIXTURES: list[tuple[int, str, str, str, int, int, str]] = [
    # impersonation — the ASCII fold of the symbol equals a reviewed symbol.
    (1, "0x357eb8dc76920a7a00d8e3059cdb0249aceb2df7", "USDC", "USD Coin", 23, 23, "impersonation"),
    (1, "0x14f01f4fd1028997fb87573c2602fc121d07a07f", "US͏DC", "US͏DC", 4, 23, "impersonation"),
    (1, "0x289d5488ab09f43471914e572ec9e3651c735af2", "COW", "CoW Protocol Token", 1, 23, "impersonation"),
    (1, "0x7452e3fc2fe611c6b7761c6c393bece059881ac7", "SAFE", "Gnosis Safe", 2, 23, "impersonation"),
    (100, "0x304a7fe17b82bb903fd1d5ca7604d5a12a5bf4e9", "USDC", "USD-SWAP˳COM", 23, 23, "impersonation"),
    (100, "0x1686edd62d367265f033eede344706b41c3a475b", "USDT", "USDTGIVEAWAYS˳COM", 2, 23, "impersonation"),
    # lure — URLs, domains (incl. the space-before-dot evasion), calls to action, $ amounts.
    (1, "0x0064f60399b93ac291894a8d7cf9d871dd93e79f",
     "Visit website yield-usd .net to claim rewards",
     "Visit website yield-usd .net to claim rewards", 23, 23, "lure"),
    (1, "0x08918171758171a13050cde6cc6eb90172af5737", "Claim Rewards On maker.gift",
     "maker.gift", 23, 23, "lure"),
    (1, "0x154c5875b1b0db1794f88d003730dad160e6b38e", "!$ Claim $200K at ETH200k.com",
     "!$ Claim $200K at ETH200k.com", 23, 23, "lure"),
    (1, "0x14d1b27d79e97e96622618f9d4fa9b1e1e9ef082", "$ ethLR.com @ $1290", "$ ethLR.com",
     23, 23, "lure"),
    (1, "0x2ec109a0cefec70661a242a8b54cae8f45630397", "apybal.com", "apybal.com", 1, 23, "lure"),
    (1, "0x6051c1354ccc51b4d561e43b02735deae64768b8", "yRise", "yRise.Finance", 8, 23, "lure"),
    (100, _A, "www.4base.cfd", "www.4base.cfd - claim Your Base airdrop", 23, 23, "lure"),
    (1, "0x1f54ac6d9634ac5370af71348d1203523b5d9172", "Earn rewards at https://reth.farm",
     "Earn rewards at https://reth.farm", 1, 23, "lure"),
    # obfuscated — invisible/combining/modifier characters or homoglyph scripts.
    (100, "0x18a4d04bd0978f62b6419780d147de958f491ad9", "SHIB", "SHIBSWAP˳ORG", 23, 23, "obfuscated"),
    (100, "0x785f9905d8c4dd3c742c96115d457c9e98b22d52", "SHIB", "cutt ̥ly/shiba", 23, 23, "obfuscated"),
    (1, _B, "ꓢꓰꓰ ꓡIꓓꓳꓦ", "ꓖꓳ ꓔꓳ", 1, 23, "obfuscated"),
    (1, _C, "V3Reward⁣.⁣c⁣o⁣m", "$", 1, 23, "obfuscated"),
    (1, _D, "ՍSD⁤С", "ՍSD⁤С", 1, 23, "obfuscated"),
    # malformed — overlong text.
    (1, _E, "X" * 40, "long", 1, 23, "malformed"),
    # mass_airdrop — held by most of the chain's active wallets, nothing else wrong.
    (1, "0x3ab281cfad326e4e93c16d99ea51cb9db27fb198", "Elon Air", "Elon Air", 23, 23, "mass_airdrop"),
    (1, "0x522a8f36e23fe1c5018e28764fea161c5f951cad", "CAT", "Trump Cat", 23, 23, "mass_airdrop"),
    (1, "0x7f3ba3f18f1378fbd8efa0a20bfe7016e2efd266", "YES", "YES", 18, 23, "mass_airdrop"),
    # --- negatives ---------------------------------------------------------------
    # Registry tokens are never spam, whatever their text says.
    (1, "0x20bc832ca081b91433ff6c17f85701b6e92486c5", "rETH2", "StakeWise Reward ETH2", 2, 23, ""),
    (100, "0x18e9262e68cc6c6004db93105cc7c001bb103e49", "RAID", "Raid Guild Token ⚔️ on xDai", 1, 23, ""),
    (1, "0x6810e776880c02933d47db1b9fc05908e5386b96", "GNO", "Gnosis Token", 8, 23, ""),
    # The RULES themselves must not fire on real names (unregistered placeholders).
    (1, _A, "rETH3", "StakeWise Reward ETH3", 1, 23, ""),
    (1, _B, "rAURA2", "Redeemed AURA", 2, 23, ""),
    (1, _C, "bIBTB", "Backed IBTB $ Treasury Bond 1-3yr", 1, 23, ""),
    (100, _D, "RAIDX", "Raid Guild Token ⚔️ on xDai", 1, 23, ""),
    (100, _E, "CANDLE", "\U0001f56f️", 1, 23, ""),
    (1, _F, "x3CRV", "Curve.fi DAI/USDC/USDT", 1, 23, ""),
    (1, _A, "SSV2", "SSV Token", 1, 23, ""),
    # Below the mass-airdrop share (7 of 23), and too few active wallets to judge.
    (1, "0x2b591e99afe9f32eaa6214f7b7629768c40eeb39", "HEX", "HEX", 7, 23, ""),
    (1, _B, "DUST", "Dust", 5, 5, ""),
]
