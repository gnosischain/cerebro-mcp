"""Number verification tool — checks arithmetic and cross-references before reporting."""

from __future__ import annotations

import json
import logging
import operator
import re
from typing import Any

from cerebro_mcp.clients.clickhouse import ClickHouseManager
from cerebro_mcp.models.custom_tool import VerificationClaim
from cerebro_mcp.runtime.tool_output import format_results_table, truncate_response

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Safe formula evaluator
# ---------------------------------------------------------------------------

_OPS: dict[str, Any] = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
}

_TOKEN_RE = re.compile(r"\s*([\+\-\*\/])\s*")


def _eval_formula(formula: str, components: dict[str, float]) -> float | str:
    """Evaluate a simple 'a - b + c' formula safely.

    Returns the computed float, or an error string if evaluation fails.
    Supports: +, -, *, / with named variables. No parentheses or functions.
    """
    formula = formula.strip()
    if not formula:
        return "empty formula"

    tokens = _TOKEN_RE.split(formula)
    # Filter empty strings from split
    tokens = [t.strip() for t in tokens if t.strip()]

    if not tokens:
        return "empty formula"

    # First token must be a component name
    first = tokens[0]
    if first not in components:
        return f"unknown component '{first}'"
    result = components[first]

    # Process operator-value pairs
    i = 1
    while i < len(tokens) - 1:
        op_str = tokens[i]
        val_name = tokens[i + 1]
        if op_str not in _OPS:
            return f"unknown operator '{op_str}'"
        if val_name not in components:
            return f"unknown component '{val_name}'"
        if op_str == "/" and components[val_name] == 0:
            return "division by zero"
        result = _OPS[op_str](result, components[val_name])
        i += 2

    return result


# ---------------------------------------------------------------------------
# Claim verification logic
# ---------------------------------------------------------------------------


def _verify_one_claim(
    claim: VerificationClaim,
    ch: ClickHouseManager | None,
) -> tuple[str, bool]:
    """Verify a single claim. Returns (formatted_output, passed)."""
    lines = [f"### Claim: \"{claim.label}\"", f"- Claimed: {claim.value}"]
    passed = True

    # --- Arithmetic check ---
    if claim.formula and claim.components:
        comp_str = ", ".join(f"{k}={v}" for k, v in claim.components.items())
        lines.append(f"- Formula: {claim.formula}")
        lines.append(f"- Components: {comp_str}")

        expected = _eval_formula(claim.formula, claim.components)
        if isinstance(expected, str):
            lines.append(f"- **FORMULA ERROR:** {expected}")
            passed = False
        else:
            lines.append(f"- Expected: {expected:.6g}")
            if claim.value == 0 and expected == 0:
                lines.append("- **ARITHMETIC PASS**")
            elif expected == 0:
                lines.append(
                    f"- **ARITHMETIC FAIL**: expected 0, got {claim.value}"
                )
                passed = False
            else:
                pct_diff = abs(claim.value - expected) / abs(expected) * 100
                if pct_diff <= claim.tolerance_pct:
                    lines.append("- **ARITHMETIC PASS**")
                else:
                    lines.append(
                        f"- **ARITHMETIC FAIL** (diff: {pct_diff:.1f}%)"
                    )
                    passed = False

    # --- Check query ---
    if claim.check_query and ch is not None:
        try:
            from cerebro_mcp.tools.governance.session_state import state

            state.record_execute_query(claim.check_query)

            executed = ch.run_query(
                claim.check_query,
                database=claim.check_database,
                requested_max_rows=5,
                audience="tool",
            )
            result = ch.build_query_result(executed, max_rows=5)

            if result.rows:
                table = format_results_table(result.columns, result.rows)
                lines.append(f"- Check query result:\n{table}")

                # Find first numeric value in check result for comparison
                check_val = _first_numeric(result.columns, result.rows)
                if check_val is not None:
                    check_pct = (
                        abs(claim.value - check_val) / abs(check_val) * 100
                        if check_val != 0
                        else (0.0 if claim.value == 0 else 100.0)
                    )
                    if check_pct <= 1.0:  # 1% tolerance for check queries
                        lines.append("- **CHECK PASS**")
                    else:
                        lines.append(
                            f"- **CHECK MISMATCH**: claimed {claim.value:.6g}, "
                            f"check shows {check_val:.6g} (diff: {check_pct:.1f}%)"
                        )
                        passed = False
            else:
                lines.append("- Check query returned no rows")
        except Exception as e:
            lines.append(f"- **CHECK FAILED:** {e}")

    # No formula and no check_query — can't verify
    if not claim.formula and not claim.check_query:
        lines.append("- No formula or check query provided — cannot verify")

    return "\n".join(lines), passed


def _first_numeric(columns: list[str], rows: list[list]) -> float | None:
    """Extract the first numeric value from the first row."""
    if not rows:
        return None
    for val in rows[0]:
        if isinstance(val, (int, float)):
            return float(val)
    return None


# ---------------------------------------------------------------------------
# MCP tool registration
# ---------------------------------------------------------------------------


def register_cross_check_tools(mcp, ch: ClickHouseManager):
    """Register the verify_numbers tool."""

    @mcp.tool()
    def verify_numbers(claims_json: str) -> str:
        """Verify numerical claims before reporting to the user.

        MUST be called before presenting any computed numbers (sums, nets,
        percentages, totals). Catches arithmetic errors and optionally
        cross-references against independent queries.

        Args:
            claims_json: JSON array of claim objects. Each has:
              - "label": description ("net GNO inflow")
              - "value": the computed number (1360.5)
              - "formula": arithmetic to verify ("received - sent")
              - "components": named values {"received": 9352.5, "sent": 9002.9}
              - "check_query": optional SQL for independent verification
              - "check_database": database for check_query (default: dbt)

        Example:
          [{"label": "net GNO inflow", "value": 1360.5,
            "formula": "received - sent",
            "components": {"received": 9352.5, "sent": 9002.9}}]

        Returns PASS (all OK) or MISMATCH (with errors and fix instructions).
        """
        try:
            raw = json.loads(claims_json)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON — {e}"

        if not isinstance(raw, list):
            return "Error: claims_json must be a JSON array of claim objects"

        claims = []
        for i, item in enumerate(raw):
            try:
                claims.append(VerificationClaim(**item))
            except Exception as e:
                return f"Error parsing claim {i}: {e}"

        if not claims:
            return "Error: No claims provided"

        sections = ["## Verification Results\n"]
        passed_count = 0
        total_count = len(claims)

        for claim in claims:
            output, ok = _verify_one_claim(claim, ch)
            sections.append(output)
            if ok:
                passed_count += 1

        sections.append("\n---")
        if passed_count == total_count:
            sections.append(
                f"**All {total_count} claims passed verification.**"
            )
        else:
            failed = total_count - passed_count
            sections.append(
                f"**{failed} of {total_count} claims FAILED verification. "
                f"Fix errors before reporting to user.**"
            )

        return truncate_response("\n\n".join(sections))
