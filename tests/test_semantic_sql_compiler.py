"""Unit tests for the semantic SQL compiler helpers.

Covers:
  * _agg_call — translates dbt MetricFlow agg names to ClickHouse
    function names (notably `count_distinct` → `uniqExact`).
  * _compile_filters — splits filters into WHERE / HAVING clauses,
    accepts the public-API `column`/`operator` keys as well as the
    internal `field`/`op` keys, and raises on unknown field names
    instead of emitting malformed `WHERE = 'val'` SQL.
"""

from __future__ import annotations

import pytest

from cerebro_mcp.semantic.sql_compiler import (
    _AGG_TO_CLICKHOUSE,
    _agg_call,
    _compile_filters,
)


# ─── _agg_call ──────────────────────────────────────────────────────


class TestAggCall:
    def test_count_distinct_translates_to_uniqExact(self):
        # The bug this patch fixes: ClickHouse has no `count_distinct`
        # function — the equivalent is `uniqExact`.
        assert _agg_call("count_distinct", "user_pseudonym") == "uniqExact(user_pseudonym)"

    def test_average_translates_to_avg(self):
        assert _agg_call("average", "x") == "avg(x)"

    def test_avg_alias_passes_through(self):
        assert _agg_call("avg", "x") == "avg(x)"

    @pytest.mark.parametrize(
        "agg,expected_fn",
        [("sum", "sum"), ("min", "min"), ("max", "max"), ("count", "count"), ("median", "median")],
    )
    def test_pass_through_aggs(self, agg, expected_fn):
        assert _agg_call(agg, "value") == f"{expected_fn}(value)"

    def test_unknown_agg_raises_with_supported_list(self):
        with pytest.raises(ValueError) as exc:
            _agg_call("count_distinct_approx", "x")
        message = str(exc.value)
        assert "Unsupported aggregation type: 'count_distinct_approx'" in message
        # Surface the supported set so authors can self-serve.
        assert "count_distinct" in message
        assert "sum" in message

    def test_expr_passed_through_verbatim(self):
        # Compound expressions (CASE, arithmetic, qualified columns) are
        # preserved unchanged in the function-call body.
        assert (
            _agg_call("sum", "CASE WHEN x > 0 THEN x ELSE 0 END")
            == "sum(CASE WHEN x > 0 THEN x ELSE 0 END)"
        )

    def test_agg_to_clickhouse_map_is_sorted_and_canonical(self):
        # Regression guard: every agg name maps to a real ClickHouse fn.
        for agg, fn in _AGG_TO_CLICKHOUSE.items():
            assert isinstance(agg, str) and agg
            assert isinstance(fn, str) and fn


# ─── _compile_filters ───────────────────────────────────────────────


class TestCompileFilters:
    def test_known_dimension_filter_goes_to_where(self):
        # The bug this patch fixes: with the previous code, when `field`
        # didn't match either lookup the fallback emitted `WHERE = 'DEX'`
        # (missing left-hand identifier). Now we either find a match or
        # raise.
        where, having = _compile_filters(
            filters=[{"column": "sector", "operator": "=", "value": "DEX"}],
            branch_dimensions={"sector": "b1_root.label"},
            metric_aliases={},
        )
        assert where == ["b1_root.label = 'DEX'"]
        assert having == []

    def test_known_metric_filter_goes_to_having(self):
        where, having = _compile_filters(
            filters=[{"field": "transaction_count", "op": ">", "value": 1000}],
            branch_dimensions={},
            metric_aliases={"transaction_count": "transaction_count"},
        )
        assert where == []
        assert having == ["transaction_count > 1000"]

    def test_accepts_internal_field_op_keys(self):
        # Backwards compat with code that constructs filters using the
        # planner-internal naming convention.
        where, _ = _compile_filters(
            filters=[{"field": "day", "op": ">=", "value": "2026-01-01"}],
            branch_dimensions={"day": "b1_root.date"},
            metric_aliases={},
        )
        assert where == ["b1_root.date >= '2026-01-01'"]

    def test_accepts_public_column_operator_keys(self):
        # Public MCP tool callers use `column` / `operator` — same filter
        # shape should work without translation in the tool layer.
        where, _ = _compile_filters(
            filters=[{"column": "day", "operator": "<=", "value": "2026-12-31"}],
            branch_dimensions={"day": "b1_root.date"},
            metric_aliases={},
        )
        assert where == ["b1_root.date <= '2026-12-31'"]

    def test_unknown_field_raises_with_valid_fields_listed(self):
        with pytest.raises(ValueError) as exc:
            _compile_filters(
                filters=[{"field": "nonsense", "op": "=", "value": 1}],
                branch_dimensions={"day": "b1_root.date", "sector": "b1_root.label"},
                metric_aliases={"transaction_count": "transaction_count"},
            )
        message = str(exc.value)
        assert "Filter field 'nonsense' is not a dimension or metric" in message
        # Valid fields should be surfaced for debugging.
        assert "day" in message and "sector" in message and "transaction_count" in message

    def test_missing_field_key_raises(self):
        with pytest.raises(ValueError, match="missing 'field' / 'column' key"):
            _compile_filters(
                filters=[{"op": "=", "value": "x"}],
                branch_dimensions={},
                metric_aliases={},
            )

    def test_default_operator_is_equals(self):
        where, _ = _compile_filters(
            filters=[{"column": "sector", "value": "DEX"}],
            branch_dimensions={"sector": "b1_root.label"},
            metric_aliases={},
        )
        assert where == ["b1_root.label = 'DEX'"]

    def test_null_value_literal(self):
        # Pseudo-NULL filter shape (`column IS NULL` analogue via `=`)
        # still produces a valid SQL literal.
        where, _ = _compile_filters(
            filters=[{"column": "day", "operator": "IS", "value": None}],
            branch_dimensions={"day": "b1_root.date"},
            metric_aliases={},
        )
        assert where == ["b1_root.date IS NULL"]

    def test_numeric_value_not_quoted(self):
        _, having = _compile_filters(
            filters=[{"field": "transaction_count", "op": ">=", "value": 5000}],
            branch_dimensions={},
            metric_aliases={"transaction_count": "transaction_count"},
        )
        assert having == ["transaction_count >= 5000"]

    def test_string_value_with_single_quote_escapes(self):
        # SQL injection guard on string values.
        where, _ = _compile_filters(
            filters=[{"column": "sector", "operator": "=", "value": "O'Brien"}],
            branch_dimensions={"sector": "b1_root.label"},
            metric_aliases={},
        )
        assert where == ["b1_root.label = 'O''Brien'"]


# ─── PR 5: time-spine upcast (cross-grain) ───────────────────────────


class TestTimeSpineUpcastCompiler:
    """The compiler must render a synthesised upcast binding as
    `<template>(<root_alias>.<col>) AS <dim>`. Planner-side coverage
    lives in test_semantic_planner.py."""

    def _build_branch_with_upcast(self):
        # Synthesise the binding shape the planner emits for "asking for
        # week on a metric whose root only has a `date` column".
        return {
            "root_model": "api_consensus_validators_active_daily",
            "metrics": ["validators_active"],
            "dimension_bindings": {
                "week": {
                    "name": "week",
                    "provider_model": "api_consensus_validators_active_daily",
                    "local": True,
                    "path": ["api_consensus_validators_active_daily"],
                    "edges": [],
                    "dimension": {
                        "name": "week",
                        "type": "time",
                        "expr": "toMonday(date)",
                        "type_params": {"time_granularity": "week"},
                        "_upcast_template": "toMonday({col})",
                        "_upcast_from_col": "date",
                        "_upcast_source_grain": "day",
                    },
                    "_synthesised": "time_spine_upcast",
                },
            },
        }

    def _build_snapshot(self):
        from types import SimpleNamespace
        return SimpleNamespace(
            models={
                "api_consensus_validators_active_daily": {
                    "name": "api_consensus_validators_active_daily",
                    "relation_name": "`dbt`.`api_consensus_validators_active_daily`",
                    "dimensions": [
                        {"name": "day", "type": "time", "expr": "date",
                         "type_params": {"time_granularity": "day"}}
                    ],
                    "measures": [
                        {"name": "validators_active_value", "agg": "sum", "expr": "cnt"}
                    ],
                },
            },
            metrics={
                "validators_active": {
                    "name": "validators_active",
                    "root_model": "api_consensus_validators_active_daily",
                    "measure": "validators_active_value",
                    "default_filters": [],
                },
            },
        )

    def test_upcast_renders_template_with_root_alias(self):
        from cerebro_mcp.semantic.sql_compiler import _compile_branch_cte
        snapshot = self._build_snapshot()
        branch = self._build_branch_with_upcast()

        sql, _, _, _ = _compile_branch_cte(
            snapshot, branch, branch_index=1, request_filters=[]
        )

        assert "toMonday(b1_root.date) AS week" in sql
        assert "sum(b1_root.cnt) AS validators_active" not in sql  # no force_qualified
        assert "sum(cnt) AS validators_active" in sql
        # The branch must GROUP BY the upcast expression, not the raw col.
        assert "GROUP BY toMonday(b1_root.date)" in sql

    def test_upcast_branch_index_propagates(self):
        # When the planner emits multiple branches, each gets its own
        # alias and the upcast template must use it.
        from cerebro_mcp.semantic.sql_compiler import _compile_branch_cte
        snapshot = self._build_snapshot()
        branch = self._build_branch_with_upcast()

        sql, _, _, _ = _compile_branch_cte(
            snapshot, branch, branch_index=2, request_filters=[]
        )

        assert "toMonday(b2_root.date) AS week" in sql
        assert "b1_root" not in sql
