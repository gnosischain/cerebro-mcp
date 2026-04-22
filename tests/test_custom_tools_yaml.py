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


def test_custom_tools_yaml_loads():
    tools = _load_tools()
    assert len(tools) >= 7, f"expected 7+ custom tools, got {len(tools)}"


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
