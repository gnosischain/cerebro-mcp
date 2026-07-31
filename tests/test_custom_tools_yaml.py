"""Regression tests for custom_tools.yaml.

Live-debugging surfaced two bugs on 2026-04-22:

1. `get_validator_balance_history` and `get_validator_withdrawals` had
   `database: consensus`, but `stg_consensus__validators` and
   `stg_consensus__withdrawals` are dbt views that live in database `dbt`.
   Every invocation raised UNKNOWN_TABLE.

2. `get_validator_withdrawals` divided `amount` by `1e18` (wei→eth factor),
   but beacon-chain withdrawal amounts are in Gwei on Gnosis. The correct
   divisor is `1e9`; the old divisor under-reported every amount by a
   factor of 1e9.

Both are now fixed; these tests lock them in.
"""

from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
YAML_PATH = REPO_ROOT / "custom_tools.yaml"


def _load_tools() -> dict[str, dict]:
    raw = yaml.safe_load(YAML_PATH.read_text())
    return {t["name"]: t for t in raw["tools"]}


def test_custom_tools_yaml_exact_count():
    """Exactly seven — the connector profile pins this set by name and SHA.

    `>=` allowed an eighth tool to slip into the advertised surface silently
    (the team_analytics_v1 profile counts 44 tools total, 7 of them from this
    file). An addition or removal must be a deliberate, reviewed change.
    """
    tools = _load_tools()
    assert len(tools) == 7, f"expected exactly 7 custom tools, got {len(tools)}"


def test_stg_consensus_tools_use_dbt_database():
    """stg_consensus__* views live in dbt, not consensus."""
    tools = _load_tools()
    for name in ("get_validator_balance_history", "get_validator_withdrawals"):
        t = tools[name]
        assert "stg_consensus__" in t["sql"], (
            f"{name} no longer references stg_consensus__* — update this test"
        )
        assert t["database"] == "dbt", (
            f"{name} has database={t['database']!r}; "
            f"stg_consensus__* live in dbt, not consensus"
        )


def test_validator_withdrawal_uses_gwei_divisor():
    """Consensus withdrawal.amount is in Gwei → divide by 1e9, not 1e18."""
    tools = _load_tools()
    sql = tools["get_validator_withdrawals"]["sql"]
    assert "amount / 1e9" in sql, (
        "get_validator_withdrawals must divide amount by 1e9 (Gwei→GNO). "
        "Using 1e18 silently under-reports every withdrawal by 1e9x."
    )
    assert "amount / 1e18" not in sql, (
        "Legacy 1e18 divisor still present; should be 1e9"
    )


def test_token_transfers_uses_effective_dated_decimals():
    """ERC-20 amounts must use per-token decimals, never a hardcoded 1e18.

    Same mistake class as the 2026-04-22 withdrawal bug above, one layer up:
    a single `/ 1e18` applied to every asset under-reports USDC (6 decimals)
    by a factor of 1e12. Decimals come from `stg_pools__tokens_meta`, which
    is EFFECTIVE-DATED (`date_start`/`date_end`) — the join must be on
    address AND date interval, with an exactly-one-match guard so an
    overlapping metadata interval cannot silently duplicate raw amounts.
    """
    tools = _load_tools()
    sql = tools["get_token_transfers_for_address"]["sql"]
    assert "1e18" not in sql, (
        "Hardcoded 1e18 divisor: wrong for every non-18-decimal token "
        "(USDC is off by 1e12). Join stg_pools__tokens_meta for decimals."
    )
    assert "stg_pools__tokens_meta" in sql, (
        "Decimals must come from stg_pools__tokens_meta (the single source "
        "of truth for token_address -> decimals), not a constant."
    )
    for needle in ("date_start", "date_end"):
        assert needle in sql, (
            f"Missing {needle!r}: the metadata join must be effective-dated "
            "(interval overlap), not a bare address join."
        )
    assert "decimals_status" in sql, (
        "Missing decimals_status: unknown/ambiguous metadata must be "
        "surfaced explicitly, never silently defaulted to 18 decimals."
    )
    assert "amount_raw" in sql, (
        "amount_raw (exact integer provenance) must be present in the output."
    )
    assert "toFloat64(amount_raw)" not in sql, (
        "Float64 normalization of Int256 raw amounts loses integer "
        "precision above 2^53; normalized amounts must be exact strings."
    )


def test_validator_tools_expose_raw_gwei():
    """Raw integer provenance must accompany converted convenience values."""
    tools = _load_tools()
    assert "balance_gwei" in tools["get_validator_balance_history"]["sql"], (
        "get_validator_balance_history must expose the raw Gwei balance "
        "alongside the converted _gno convenience columns."
    )
    assert "amount_gwei" in tools["get_validator_withdrawals"]["sql"], (
        "get_validator_withdrawals must expose the raw Gwei amount "
        "alongside the converted _gno convenience column."
    )


def test_deposit_events_stay_raw():
    """GBC deposit amounts stay in raw event units — no invented divisor.

    The correct Gwei/mGNO conversion for Gnosis deposit-contract events is
    era-dependent; a guessed constant here is exactly the bug class this
    file exists to prevent. Raw sums are exact (UInt64); conversion is the
    caller's explicit, documented decision.
    """
    sql = _load_tools()["get_deposit_events"]["sql"]
    assert "1e9" not in sql and "1e18" not in sql, (
        "get_deposit_events must not apply a unit divisor; amounts are raw "
        "deposit-event units and any conversion must be explicit upstream."
    )
